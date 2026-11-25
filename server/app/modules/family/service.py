"""Family care flow.

Consent lives on the parent's side: the PARENT creates the invite and chooses what is shared,
can change it or stop it at any time, and approves every reminder a relative proposes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

import app.db.models  # noqa: F401
from app.core.config import settings
from app.modules.auth.models import User
from app.modules.family.models import FamilyEvent, FamilyItem, FamilyLink, FamilyRequest
from app.modules.push.models import PushSubscription
from app.modules.push.service import Sender, default_sender
from app.shared.api import AppError

logger = logging.getLogger(__name__)
MAX_CAREGIVERS, MAX_MEMBERS, MISSED_AFTER, FEED_WINDOW = 4, 6, timedelta(hours=2), timedelta(hours=18)
_FAILED: dict[str, list[float]] = {}   # join attempts per caregiver (brute-force guard)


def _sha(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Shares(_M):
    meds: bool = True
    appts: bool = True
    emergency: bool = True


class InviteIn(_M):
    name: str = Field(min_length=1, max_length=60)
    shares: Shares = Shares()


class JoinIn(_M):
    code: str = Field(min_length=6, max_length=20)
    label: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=60)


class SyncItem(_M):
    client_id: str = Field(min_length=1, max_length=100)
    title: str = Field(min_length=1, max_length=120)
    at: datetime


class SyncIn(_M):
    doses: list[SyncItem] = Field(default_factory=list, max_length=200)
    appts: list[SyncItem] = Field(default_factory=list, max_length=100)


class RequestIn(_M):
    link_id: uuid.UUID
    title: str = Field(min_length=1, max_length=160)
    note: str | None = Field(default=None, max_length=300)
    due: datetime | None = None


class FamilyService:
    def __init__(self, session: AsyncSession, *, clock: Callable[[], datetime] | None = None) -> None:
        self._s = session
        self._clock = clock or (lambda: datetime.now(UTC))

    # ---------------- parent side ----------------
    async def invite(self, member: User, body: InviteIn) -> str:
        n = await self._s.scalar(select(func.count()).select_from(FamilyLink).where(FamilyLink.member_id == member.id, FamilyLink.status == "active"))
        if (n or 0) >= MAX_CAREGIVERS:
            raise AppError(409, "too_many_caregivers", f"at most {MAX_CAREGIVERS} relatives can be linked")
        await self._s.execute(delete(FamilyLink).where(FamilyLink.member_id == member.id, FamilyLink.status == "pending"))
        code = f"{secrets.randbelow(10**8):08d}"
        self._s.add(FamilyLink(member_id=member.id, invite_hash=_sha(code), invite_expires=self._clock() + timedelta(hours=24),
                               member_name=body.name.strip(), share_meds=body.shares.meds, share_appts=body.shares.appts,
                               share_emergency=body.shares.emergency))
        await self._s.commit()
        return code

    async def set_shares(self, member: User, link_id: uuid.UUID, shares: Shares) -> None:
        link = await self._own_member_link(member, link_id)
        link.share_meds, link.share_appts, link.share_emergency = shares.meds, shares.appts, shares.emergency
        await self._s.commit()

    async def sync(self, member: User, body: SyncIn) -> dict[str, int]:
        """The phone owns the schedule. Only kinds that at least one active link shares are stored at all."""
        links = await self._active_links_of_member(member.id)
        want = {"dose": any(link.share_meds for link in links), "appt": any(link.share_appts for link in links)}
        now, out = self._clock(), {"doses": 0, "appts": 0}
        for kind, items, key in (("dose", body.doses, "doses"), ("appt", body.appts, "appts")):
            existing = {i.client_id: i for i in (await self._s.scalars(select(FamilyItem).where(FamilyItem.member_id == member.id, FamilyItem.kind == kind))).all()}
            keep: set[str] = set()
            if want[kind]:
                for it in items:
                    if it.at < now - timedelta(hours=24) or it.at > now + timedelta(days=30):
                        continue
                    keep.add(it.client_id)
                    row = existing.get(it.client_id)
                    if row is None:
                        self._s.add(FamilyItem(member_id=member.id, kind=kind, client_id=it.client_id, title=it.title.strip(), at=it.at))
                    else:
                        row.title, row.at = it.title.strip(), it.at
            for cid, row in existing.items():   # gone from the phone (deleted or no longer shared) -> gone here
                if cid not in keep and row.done_at is None:
                    await self._s.delete(row)
            out[key] = len(keep)
        await self._s.commit()
        return out

    async def taken(self, member: User, client_id: str, taken: bool = True) -> None:
        row = (await self._s.execute(select(FamilyItem).where(FamilyItem.member_id == member.id, FamilyItem.kind == "dose", FamilyItem.client_id == client_id))).scalar_one_or_none()
        if row is None:
            return
        row.done_at = self._clock() if taken else None
        if taken:
            self._s.add(FamilyEvent(member_id=member.id, kind="dose_taken", title=row.title))
        await self._s.commit()

    async def emergency(self, member: User, sender: Sender | None = None) -> int:
        links = [link for link in await self._active_links_of_member(member.id) if link.share_emergency]
        if not links:
            return 0
        self._s.add(FamilyEvent(member_id=member.id, kind="emergency", title="طلب مساعدة طارئة"))
        await self._s.commit()
        return await self._notify(links, "🚨 طلب مساعدة من {who}", "افتح نسيم واتصل دلوقتي.", sender)

    async def incoming(self, member: User) -> list[dict[str, Any]]:
        rows = (await self._s.execute(select(FamilyRequest, FamilyLink).join(FamilyLink, FamilyRequest.link_id == FamilyLink.id)
                                      .where(FamilyLink.member_id == member.id, FamilyLink.status == "active", FamilyRequest.status == "pending")
                                      .order_by(FamilyRequest.created_at))).all()
        return [{"id": str(r.id), "from": link.caregiver_name or "حد من العيلة", "title": r.title, "note": r.note,
                 "due": r.due.isoformat() if r.due else None} for r, link in rows]

    async def decide(self, member: User, request_id: uuid.UUID, accept: bool) -> None:
        row = (await self._s.execute(select(FamilyRequest, FamilyLink).join(FamilyLink, FamilyRequest.link_id == FamilyLink.id)
                                     .where(FamilyRequest.id == request_id, FamilyLink.member_id == member.id))).first()
        if row is None or row[0].status != "pending":
            raise AppError(404, "not_found", "no pending request with this id")
        req = row[0]
        req.status, req.decided_at = ("accepted" if accept else "rejected"), self._clock()
        self._s.add(FamilyEvent(member_id=member.id, kind="request_accepted" if accept else "request_rejected", title=req.title))
        await self._s.commit()

    # ---------------- relative side ----------------
    async def join(self, caregiver: User, body: JoinIn) -> FamilyLink:
        key, now = str(caregiver.id), time.time()
        recent = [t for t in _FAILED.get(key, []) if now - t < 600]
        if len(recent) >= 5:
            raise AppError(429, "too_many_attempts", "too many wrong codes; try again in 10 minutes")
        code = "".join(ch for ch in body.code if ch.isdigit())
        link = (await self._s.execute(select(FamilyLink).where(FamilyLink.invite_hash == _sha(code), FamilyLink.status == "pending"))).scalar_one_or_none()
        if link is None or (link.invite_expires and link.invite_expires < self._clock()):
            _FAILED[key] = recent + [now]
            raise AppError(404, "bad_code", "the code is wrong or expired")
        if link.member_id == caregiver.id:
            raise AppError(400, "self_link", "you cannot link to yourself")
        dup = await self._s.scalar(select(func.count()).select_from(FamilyLink).where(FamilyLink.member_id == link.member_id, FamilyLink.caregiver_id == caregiver.id, FamilyLink.status == "active"))
        if dup:
            raise AppError(409, "already_linked", "already linked")
        mine = await self._s.scalar(select(func.count()).select_from(FamilyLink).where(FamilyLink.caregiver_id == caregiver.id, FamilyLink.status == "active"))
        if (mine or 0) >= MAX_MEMBERS:
            raise AppError(409, "too_many_members", f"at most {MAX_MEMBERS} relatives")
        link.caregiver_id, link.member_label, link.caregiver_name = caregiver.id, body.label.strip(), body.name.strip()
        link.status, link.joined_at, link.invite_hash, link.invite_expires = "active", self._clock(), None, None
        await self._s.commit()
        return link

    async def feed(self, caregiver: User) -> list[dict[str, Any]]:
        now = self._clock()
        day0 = now - FEED_WINDOW
        out = []
        for link in (await self._s.scalars(select(FamilyLink).where(FamilyLink.caregiver_id == caregiver.id, FamilyLink.status == "active"))).all():
            doses, appts = [], []
            if link.share_meds:
                for d in (await self._s.scalars(select(FamilyItem).where(FamilyItem.member_id == link.member_id, FamilyItem.kind == "dose",
                                                                          FamilyItem.at >= day0, FamilyItem.at <= now + timedelta(hours=18)).order_by(FamilyItem.at))).all():
                    state = "taken" if d.done_at else ("missed" if d.at < now - MISSED_AFTER else ("late" if d.at < now else "upcoming"))
                    doses.append({"title": d.title, "at": d.at.isoformat(), "state": state})
            if link.share_appts:
                appts = [{"title": a.title, "at": a.at.isoformat()} for a in (await self._s.scalars(
                    select(FamilyItem).where(FamilyItem.member_id == link.member_id, FamilyItem.kind == "appt", FamilyItem.at >= now,
                                             FamilyItem.at <= now + timedelta(days=14)).order_by(FamilyItem.at).limit(10))).all()]
            kinds = ["request_accepted", "request_rejected"] + (["dose_taken", "dose_missed"] if link.share_meds else []) + (["emergency"] if link.share_emergency else [])
            events = [{"kind": e.kind, "title": e.title, "at": e.created_at.isoformat()} for e in (await self._s.scalars(
                select(FamilyEvent).where(FamilyEvent.member_id == link.member_id, FamilyEvent.kind.in_(kinds),
                                          FamilyEvent.created_at >= max(now - timedelta(days=3), link.joined_at or now)).order_by(FamilyEvent.created_at.desc()).limit(15))).all()]
            reqs = [{"title": r.title, "status": r.status, "due": r.due.isoformat() if r.due else None} for r in (await self._s.scalars(
                select(FamilyRequest).where(FamilyRequest.link_id == link.id).order_by(FamilyRequest.created_at.desc()).limit(5))).all()]
            out.append({"link_id": str(link.id), "label": link.member_label or link.member_name, "name": link.member_name,
                        "shares": {"meds": link.share_meds, "appts": link.share_appts, "emergency": link.share_emergency},
                        "doses": doses, "appts": appts, "events": events, "requests": reqs})
        return out

    async def propose(self, caregiver: User, body: RequestIn) -> None:
        link = await self._s.get(FamilyLink, body.link_id)
        if link is None or link.caregiver_id != caregiver.id or link.status != "active":
            raise AppError(404, "not_found", "no active link")
        pend = await self._s.scalar(select(func.count()).select_from(FamilyRequest).where(FamilyRequest.link_id == link.id, FamilyRequest.status == "pending"))
        if (pend or 0) >= 20:
            raise AppError(429, "too_many_requests", "too many pending requests")
        self._s.add(FamilyRequest(link_id=link.id, title=body.title.strip(), note=(body.note or "").strip() or None, due=body.due))
        await self._s.commit()

    # ---------------- both sides ----------------
    async def links(self, user: User) -> dict[str, Any]:
        rows = (await self._s.scalars(select(FamilyLink).where(or_(FamilyLink.member_id == user.id, FamilyLink.caregiver_id == user.id),
                                                                 FamilyLink.status.in_(["pending", "active"])))).all()
        now = self._clock()
        return {
            "caregivers": [{"id": str(x.id), "name": x.caregiver_name, "since": x.joined_at.isoformat() if x.joined_at else None,
                            "shares": {"meds": x.share_meds, "appts": x.share_appts, "emergency": x.share_emergency}}
                           for x in rows if x.member_id == user.id and x.status == "active"],
            "pending_invite": next(({"expires": x.invite_expires.isoformat()} for x in rows if x.member_id == user.id and x.status == "pending"
                                    and x.invite_expires and x.invite_expires > now), None),
            "following": [{"id": str(x.id), "label": x.member_label or x.member_name} for x in rows if x.caregiver_id == user.id and x.status == "active"],
        }

    async def revoke(self, user: User, link_id: uuid.UUID) -> None:
        link = await self._s.get(FamilyLink, link_id)
        if link is None or user.id not in (link.member_id, link.caregiver_id):
            raise AppError(404, "not_found", "link not found")
        link.status, link.revoked_at = "revoked", self._clock()
        await self._s.commit()

    # ---------------- background: missed doses ----------------
    async def check_missed(self, sender: Sender | None = None) -> int:
        now = self._clock()
        due = (await self._s.scalars(select(FamilyItem).where(FamilyItem.kind == "dose", FamilyItem.done_at.is_(None), FamilyItem.alerted_at.is_(None),
                                                              FamilyItem.at <= now - MISSED_AFTER, FamilyItem.at >= now - FEED_WINDOW))).all()
        sent = 0
        for d in due:
            d.alerted_at = now
            # only relatives who were already linked when the dose was due (no alerts about the past)
            links = [link for link in await self._active_links_of_member(d.member_id) if link.share_meds and link.joined_at and link.joined_at <= d.at]
            if links:
                self._s.add(FamilyEvent(member_id=d.member_id, kind="dose_missed", title=d.title))
            await self._s.commit()
            sent += await self._notify(links, "نسيم العيلة: {who}", "فيه جرعة دوا فاتت من ساعتين. اطمّن بمكالمة.", sender)
        await self._s.commit()
        return sent

    # ---------------- helpers ----------------
    async def _own_member_link(self, member: User, link_id: uuid.UUID) -> FamilyLink:
        link = await self._s.get(FamilyLink, link_id)
        if link is None or link.member_id != member.id or link.status != "active":
            raise AppError(404, "not_found", "link not found")
        return link

    async def _active_links_of_member(self, member_id: uuid.UUID) -> list[FamilyLink]:
        return list((await self._s.scalars(select(FamilyLink).where(FamilyLink.member_id == member_id, FamilyLink.status == "active"))).all())

    async def _notify(self, links: list[FamilyLink], title: str, body: str, sender: Sender | None) -> int:
        """Plain push to the relatives. Kept generic on purpose: no medicine names travel through push services."""
        if not (settings.vapid_public_key and settings.vapid_private_key) and sender is None:
            return 0
        send, n = sender or default_sender, 0
        for link in links:
            if link.caregiver_id is None:
                continue
            payload = json.dumps({"p": {"t": title.format(who=link.member_label or link.member_name), "b": body}, "id": "fam-" + str(link.id)}, ensure_ascii=False)
            for sub in (await self._s.scalars(select(PushSubscription).where(PushSubscription.user_id == link.caregiver_id))).all():
                try:
                    await asyncio.to_thread(send, {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}, payload)
                    n += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("family push failed: %s", exc)
        return n


async def purge_family(session: AsyncSession, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    await session.execute(delete(FamilyEvent).where(FamilyEvent.created_at < now - timedelta(days=30)))
    await session.execute(delete(FamilyItem).where(FamilyItem.at < now - timedelta(days=3)))
    await session.execute(delete(FamilyLink).where(FamilyLink.status == "pending", FamilyLink.invite_expires < now))
    await session.commit()
