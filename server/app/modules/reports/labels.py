"""Arabic labels and date formatting shared by PDF and Excel outputs."""

from __future__ import annotations

from datetime import datetime

from app.modules.approvals.models import PendingAction
from app.shared.enums import ApprovalActionType, MemoryCategory, SourceType

CATEGORY_AR: dict[MemoryCategory, str] = {
    MemoryCategory.PERSONAL: "شخصي",
    MemoryCategory.PREFERENCE: "تفضيلات",
    MemoryCategory.RELATIONSHIP: "علاقات",
    MemoryCategory.HEALTH: "صحة",
    MemoryCategory.WORK: "شغل",
    MemoryCategory.FINANCE: "فلوس",
    MemoryCategory.OTHER: "أخرى",
}
SOURCE_AR: dict[SourceType, str] = {
    SourceType.VOICE: "صوت",
    SourceType.TEXT: "كتابة",
    SourceType.INFERRED: "استنتاج",
    SourceType.INTEGRATION: "تكامل",
    SourceType.MANUAL: "يدوي",
}
PERIOD_AR = {"week": "الأسبوعي", "month": "الشهري"}
_DAYS_AR = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]


def entity_ar(entity: str) -> str:
    return "أنت" if entity == "user" else entity


# Keys are stored as English snake_case (stable for matching); show common ones in Arabic.
_KEY_AR = {
    "city": "المدينة", "address": "العنوان", "neighborhood": "المنطقة", "country": "البلد",
    "birthday": "عيد الميلاد", "anniversary": "ذكرى الجواز", "age": "السن",
    "relation": "صلة القرابة", "relationship": "صلة القرابة", "spouse": "الزوج/الزوجة",
    "employer": "جهة العمل", "job_title": "الوظيفة", "role": "الدور", "company": "الشركة",
    "favorite_drink": "المشروب المفضل", "favorite_food": "الأكل المفضل", "favorite_color": "اللون المفضل",
    "hobby": "الهواية", "hobbies": "الهوايات", "sport": "الرياضة", "pet": "الحيوان الأليف",
    "phone": "التليفون", "email": "الإيميل", "car": "العربية",
    "blood_type": "فصيلة الدم", "allergy": "حساسية", "allergies": "حساسية", "medication": "دوا",
    "school": "المدرسة", "university": "الجامعة", "language": "اللغة", "religion": "الديانة",
}


def key_ar(key: str) -> str:
    return _KEY_AR.get(key, key.replace("_", " "))


def fmt_date(dt: datetime | None) -> str:
    if dt is None:
        return "من غير معاد"
    return f"{_DAYS_AR[dt.weekday()]} {dt.day}/{dt.month}/{dt.year}"


def fmt_datetime(dt: datetime | None) -> str:
    if dt is None:
        return "من غير معاد"
    hour = dt.hour % 12 or 12
    suffix = "ص" if dt.hour < 12 else "م"
    return f"{fmt_date(dt)}، {hour}:{dt.minute:02d} {suffix}"


def describe_action(a: PendingAction) -> tuple[str, str]:
    """(kind, details) for an approval row."""
    p = a.payload
    if a.action_type == ApprovalActionType.UPDATE_MEMORY:
        return "تعديل معلومة", f"{key_ar(str(p.get('attribute_key', '')))}: {p.get('current_value', '')} ← {p.get('proposed_value', '')}"
    title = str(p.get("title", ""))
    return "التزام جديد", title
