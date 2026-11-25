/* Nasem app tests: a real browser opens the app and checks behavior end to end.
   Run:  cd tests && npm install && npm test        (see README.md)
   Every check prints ✓ or ✗; the process exits with 1 if anything failed. */
"use strict";
const http = require("http"), fs = require("fs"), path = require("path");

const ROOT = path.resolve(__dirname, "..");
const PORT = +(process.env.PORT || 8099);
const API = process.env.NASEM_API || "";            // optional: a running server for the backup test
let pass = 0, fail = 0;
const check = (name, ok, extra) => { if (ok) pass++; else fail++; console.log(`${ok ? "✓" : "✗"} ${name}${!ok && extra !== undefined ? "  →  " + JSON.stringify(extra).slice(0, 300) : ""}`); };

function serve(){
  const types = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".json": "application/json", ".webmanifest": "application/manifest+json", ".png": "image/png"};
  return http.createServer((req, res) => {
    let p = decodeURIComponent(req.url.split("?")[0]).replace(/^\/nasem/, "");
    if (p === "/" || p === "") p = "/index.html";
    if (p === "/config.json"){ res.writeHead(200, {"content-type": types[".json"]}); res.end(JSON.stringify({server: API})); return; }
    const f = path.join(ROOT, p);
    if (!f.startsWith(ROOT) || !fs.existsSync(f) || fs.statSync(f).isDirectory()){ res.writeHead(404); res.end(); return; }
    res.writeHead(200, {"content-type": types[path.extname(f)] || "application/octet-stream"}); fs.createReadStream(f).pipe(res);
  }).listen(PORT);
}
async function launch(){
  try{ const pp = require("puppeteer"); return await pp.launch({headless: true, args: ["--no-sandbox"]}); }
  catch(e){
    const chromium = require("@sparticuz/chromium"), pc = require("puppeteer-core");
    return pc.launch({executablePath: await chromium.executablePath(), args: chromium.args.filter(a => !/single-process|no-zygote/.test(a)), headless: true});
  }
}
async function openApp(b, {state, lock} = {}){
  const ctx = await b.createBrowserContext(), p = await ctx.newPage();
  await p.emulateTimezone("Africa/Cairo"); await p.setViewport({width: 393, height: 1500, isMobile: true});
  p.errs = []; p.on("pageerror", e => p.errs.push(e.message)); p.on("dialog", d => d.accept());
  await p.setRequestInterception(true);
  p.on("request", r => /frankfurter|xaus\.com/.test(r.url()) ? r.abort() : r.continue());
  if (state) await p.evaluateOnNewDocument(s => { localStorage.setItem("nasem-demo-storage", "device"); localStorage.setItem("nasem-demo-state", s); }, JSON.stringify(state));
  await p.goto(`http://localhost:${PORT}/nasem/`, {waitUntil: "networkidle0"});
  await p.waitForFunction(() => typeof ready !== "undefined" && ready, {timeout: 20000});
  if (!lock) await p.evaluate(() => {
    S.consent = {version: 1, at: ""}; S.profile.onboarded = "x"; S.settings.sensitive = true; document.getElementById("sheetBack").hidden = true;
    // AI is never called in these tests: anything that reaches it answers "AI"
    samplePromise = Promise.resolve({json: async () => ({agent_reply: "AI", extracted_memories: [], extracted_commitments: [], approval_decisions: [], completed_commitments: [], forget_memories: [], guide_request: null, learned: []})});
  });
  return p;
}
const say = (p, q) => p.evaluate(async q => { await send(q); const m = S.messages.at(-1); return {local: !!m.local, text: m.text, links: (m.links || []).map(l => l.label)}; }, q);

(async () => {
  const srv = serve(), b = await launch();
  try{
    /* ---------- 1. the app opens, nothing covers it ---------- */
    let p = await openApp(b);
    check("التطبيق بيفتح من غير أخطاء", p.errs.length === 0, p.errs);
    check("مفيش حاجة مغطية الشاشة (القفل مخفي)", await p.evaluate(() => document.elementFromPoint(200, 300).closest("#lockView") === null));
    check("مفيش سكرول عرضي على 393px", await p.evaluate(() => document.documentElement.scrollWidth <= 393));

    /* ---------- 2. نسيم الحسابات ---------- */
    let r = await say(p, "محمد البقال أخد سكر وزيت بـ 500 شكك");
    check("زبون أخد شكك بيتسجل محلي", r.local && /500|٥٠٠/.test(r.text), r);
    r = await say(p, "محمد اخد علبتين سجاير بمية وخمسين على الحساب");
    check("الأرقام بالكلام (بمية وخمسين = 150)", await p.evaluate(() => accBalance(S.accounts[0]) === 650), r);
    r = await say(p, "محمد دفع 200");
    check("دفعة بتنقص الحساب وفيها كشف حساب واتساب", r.local && r.links.length === 1 && await p.evaluate(() => accBalance(S.accounts[0]) === 450), r);
    r = await say(p, "خدت بضاعة من الحاج سعيد بـ 3000 على الحساب");
    r = await say(p, "دفعت للحاج سعيد ألف وخمسمية");
    check("مورد: بضاعة ودفعة (ألف وخمسمية)", await p.evaluate(() => accBalance(S.accounts.find(a => a.kind === "supplier")) === 1500), r);
    r = await say(p, "عليا كام للحاج سعيد");
    check("سؤال عن حساب مورد", /الحاج سعيد/.test(r.text) && !/مفيش/.test(r.text), r);
    r = await say(p, "محمد هيدفع الخميس");
    check("ميعاد دفع بيتسجل", await p.evaluate(() => !!S.accounts[0].promise), r);
    r = await say(p, "صرفت 50 جنيه مواصلات");
    check("المصروف العادي ما بيدخلش الحسابات", r.local && /مواصلات/.test(r.text) && await p.evaluate(() => S.accounts.length === 2), r);
    r = await say(p, "سلفت كريم 500 جنيه");
    check("السلف بتروح للذكاء زي الأول", !r.local, r);

    /* ---------- 3. الجمعيات ---------- */
    await p.evaluate(() => {
      const m = new Date(); m.setMonth(m.getMonth() - 1);
      S.ledger.gam3eyas.push({id: "g2", name: "جمعية العيلة", monthly: "500", start: localISO(m).slice(0, 7), payDay: 1, role: "organizer", paid: {},
        people: [{id: "p1", name: "منى"}, {id: "p2", name: "هبة"}, {id: "p3", name: "أنا", isMe: true}, {id: "p4", name: "خالد"}]});
      gamSync(S.ledger.gam3eyas[0]);
    });
    r = await say(p, "منى دفعت الجمعية");
    check("المنظم بيسجل دفع عضو", r.local && /منى/.test(r.text) && /هبة/.test(r.text), r);
    r = await say(p, "مين لسه مدفعش الجمعية");
    check("مين ما دفعش + تذكير من غير أسامي", r.local && r.links.some(l => /من غير أسامي/.test(l)), r);
    check("نص التذكير للجروب ما فيهوش أسامي اللي ما دفعوش (بس اللي بيقبض)", await p.evaluate(() => { const t = gamReminderText(S.ledger.gam3eyas[0]); return !/خالد/.test(t) && /هبة/.test(t); }));

    /* ---------- 4. الصنايعي ---------- */
    r = await say(p, "السباك أحمد صلح الحنفية بـ 300 وضمان شهر وكان ممتاز");
    check("شغلانة صنايعي بالضمان والتقييم", await p.evaluate(() => { const c = S.crafts[0]; return c.name === "أحمد" && c.jobs[0].rating === 5 && !!c.jobs[0].warrantyUntil; }), r);
    r = await say(p, "الحنفية بتنقط تاني");
    check("حاجة باظت وهي في الضمان", r.local && /ضمان/.test(r.text), r);
    r = await say(p, "السباك عايز 450 عشان يصلح الحنفية");
    check("مقارنة سعر الصنايعي بآخر مرة", r.local && /٥٠٪|50/.test(r.text), r);
    r = await say(p, "صرفت 300 على السباك");
    check("صرفت على السباك = مصروف مش شغلانة", await p.evaluate(() => S.crafts[0].jobs.length === 1), r);

    /* ---------- 5. البحث والمساعدة والتقرير ---------- */
    r = await say(p, "دوّر على الحاج سعيد");
    check("البحث بالصوت (بالشدة)", r.local && /حسابات/.test(r.text), r);
    check("البحث بيلاقي المصروف", await p.evaluate(() => searchAll("مواصلات").some(x => x.g === "مصروف")));
    r = await say(p, "تقدر تعمل ايه");
    check("سؤال المساعدة", r.local && r.links.length === 1, r);
    const rep = await p.evaluate(() => monthReport(new Date(new Date().getFullYear(), new Date().getMonth(), 1)));
    check("تقرير الشهر فيه المصروف والحسابات والجمعيات والصنايعية", /مصروف البيت/.test(rep) && /الحسابات/.test(rep) && /الجمعيات/.test(rep) && /الصنايعية/.test(rep), rep);
    await p.evaluate(() => { switchTab("money"); render(); });
    check("صفحة فلوسي فيها كل الأقسام", await p.evaluate(() => ["sec-acc", "sec-gam", "sec-loans", "sec-spend", "sec-market", "sec-proj"].every(id => document.getElementById(id))));
    check("كل قسم معاه أمثلة صوتية", await p.evaluate(() => document.querySelectorAll("#panel-money .say").length >= 5));
    check("مفيش أخطاء في الصفحة", p.errs.length === 0, p.errs);
    await p.browserContext().close();

    /* ---------- 5ب. البحث الذكي ---------- */
    p = await openApp(b);
    const rs = await p.evaluate(async () => {
      const calls = []; let mode = "search";
      samplePromise = Promise.resolve({json: async (pr, opts) => { calls.push({pr, opts});
        const rep = {title: "تجديد جواز السفر", bottom_line: "الرسوم حوالي 1,200 جنيه.", as_of: "2026-11-10", confidence: "high", key_numbers: [{label: "الرسوم", value: "1,200 جنيه", note: ""}],
          table: null, sections: [], what_to_do: ["احجز ميعاد"], watch_out: [], disagreements: null, follow_ups: ["فين أقرب مكتب؟"],
          sources: [{title: "بوابة مصر الرقمية", url: "https://digital.gov.eg/passport", date: "2026-11-10"}, {title: "مخترع", url: "https://fake-site.example/x", date: null}]};
        if (mode === "nosearch") return rep;
        return {...rep, _sources: [{url: "https://vertexaisearch.cloud.google.com/grounding-api-redirect/A", title: "digital.gov.eg", cited: true}]};
      }});
      const R = {};
      R.kinds = ["تجديد الباسبور", "آخر أخبار البنزين", "سعر التكييف", "أعراض الضغط", "حكم زكاة الدهب", "قانون الإيجار", "أحسن موبايل"].map(q => researchKind(q).k).join(",");
      R.noHijack = !researchIntent("فكرني أكلم أمي بكرة") && !researchIntent("الدولار بكام");
      R.compare = researchIntent("قارنلي بين أيفون وسامسونج").kind.k;
      await send("ابحث على النت عن تجديد الباسبور");
      const rec = S.reports[0];
      R.webSearch = calls[0].opts.webSearch === true; R.govHint = /gov\.eg/.test(calls[0].pr); R.noPersonal = !/known_memories|recent_conversation/.test(calls[0].pr);
      R.official = rec.sources.some(s => s.official && s.host === "digital.gov.eg"); R.fakeDropped = !rec.sources.some(s => /fake-site/.test(s.url));
      const n = calls.length; await send("ابحث على النت عن تجديد الباسبور"); R.cached = calls.length === n && !!S.messages.at(-1).researchCached;
      mode = "nosearch"; await send("اعملي تقرير عن أسعار التكييفات"); R.noSearchFlag = S.reports[0].searched === false && S.reports[0].sources.length === 0;
      return R;
    });
    check("البحث بيختار المكان الصح لكل نوع سؤال", rs.kinds === "gov,news,prices,health,religion,law,compare", rs.kinds);
    check("البحث الذكي ما بيخطفش الكلام العادي ولا سؤال الدولار", rs.noHijack);
    check("قارنلي = مقارنة", rs.compare === "compare");
    check("البحث بيستخدم النت ويتوجه لمواقع الحكومة", rs.webSearch && rs.govHint, rs);
    check("البحث ما بيبعتش بياناتك الشخصية", rs.noPersonal);
    check("المصدر الرسمي متعلّم عليه", rs.official, rs);
    check("اللينك المخترع اتشال", rs.fakeDropped, rs);
    check("نفس السؤال تاني بيرجع من التقرير المحفوظ من غير ما يصرف من الحصة", rs.cached, rs);
    check("لو المزود ما دوّرش على النت، التقرير بيقول كده", rs.noSearchFlag, rs);
    check("كارت التقرير بيظهر من غير أخطاء", await p.evaluate(() => document.querySelectorAll(".research").length >= 1) && p.errs.length === 0, p.errs);
    await p.browserContext().close();

    /* ---------- 6. بيانات بايظة ما بتوقعش التطبيق ---------- */
    p = await openApp(b, {state: {consent: {version: 1, at: ""}, settings: {sensitive: true}, memories: [], commitments: [],
      accounts: [{id: "bad", name: "x"}, {name: "محمد", entries: [{type: "charge", amount: "500", at: new Date().toISOString()}, {type: "zz"}]}, null],
      crafts: [{name: "أحمد"}], spend: [{at: "2026-11-01", amount: "abc"}], ledger: {gam3eyas: [{name: "ج", role: "organizer", start: "2026-10", monthly: "100"}]}, messages: [{text: 5}]}});
    await p.evaluate(() => { document.getElementById("sheetBack").hidden = true; switchTab("money"); render(); switchTab("memory"); render(); });
    check("بيانات بايظة: الصفحات بتظهر والبيانات السليمة فاضلة", await p.evaluate(() => !!document.getElementById("sec-crafts") && S.accounts.length === 2 && accBalance(S.accounts[1]) === 500), p.errs);
    await p.browserContext().close();

    /* ---------- 7. القفل ---------- */
    p = await openApp(b);
    await p.evaluate(async () => { const salt = b64(crypto.getRandomValues(new Uint8Array(16))); setLockCfg({salt, hash: await pinHash("2580", salt), len: 4, after: 1, cred: null}); });
    await p.reload({waitUntil: "networkidle0"});
    check("بعد الفتح: القفل ظاهر والتطبيق مخفي", await p.evaluate(() => !document.getElementById("lockView").hidden && getComputedStyle(document.querySelector(".app")).visibility === "hidden"));
    await p.type(".lock-pin", "1111"); await new Promise(r => setTimeout(r, 900));
    check("رقم غلط ما بيفتحش", await p.evaluate(() => locked));
    await p.type(".lock-pin", "2580"); await new Promise(r => setTimeout(r, 900));
    check("الرقم الصح بيفتح", await p.evaluate(() => !locked && getComputedStyle(document.querySelector(".app")).visibility === "visible"));
    const bf = await p.evaluate(async () => { lockNow(); const pin = document.querySelector(".lock-pin"); for (let i = 0; i < 6; i++){ pin.value = "000" + i; pin.dispatchEvent(new Event("input")); await new Promise(r => setTimeout(r, 500)); } return document.querySelector(".lock-msg").textContent; });
    check("بعد 5 محاولات غلط لازم يستنى", /استنى/.test(bf), bf);
    await p.browserContext().close();

    /* ---------- 8. النسخة الاحتياطية المتشفرة (من غير سيرفر) ---------- */
    p = await openApp(b);
    const bk = await p.evaluate(async () => {
      await send("محمد البقال أخد سكر وزيت بـ 500 شكك");
      const blob = await NASEM_APP.backup.sealWithPass(snapshotState(), "بيتنا في شبرا 1990");
      let wrong = false; try{ await NASEM_APP.backup.open(blob, "غلط"); }catch(e){ wrong = e.code === "wrong_passphrase"; }
      const back = await NASEM_APP.backup.open(blob, "بيتنا في شبرا 1990");
      return {plainLeak: /محمد|memories/.test(blob), wrong, restored: back.accounts[0].name};
    });
    check("الملف المتشفر ما فيهوش أي كلام مقروء", !bk.plainLeak, bk);
    check("كلمة سر غلط بترفض", bk.wrong, bk);
    check("كلمة السر الصح بترجّع البيانات", bk.restored === "محمد البقال", bk);
    await p.browserContext().close();

    if (API){
      p = await openApp(b);
      const email = `t${Date.now()}@nasem.dev`, pw = "correct-horse-battery";
      await fetch(API + "/v1/auth/register", {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({email, password: pw})});
      const ok = await p.evaluate(async (API, email, pw) => {
        NASEM_APP.setConfig({...NASEM_APP.getConfig(), mode: "gateway"}); await NASEM_APP.gateway.login(API, email, pw);
        await send("محمد البقال أخد سكر وزيت بـ 500 شكك"); await NASEM_APP.backup.setup("بيتنا في شبرا 1990"); S.settings.autoBackup = true;
        const done = await cloudBackupNow(true), r = await NASEM_APP.backup.download(false);
        return done && !/محمد/.test(r.blob) && (await NASEM_APP.backup.open(r.blob, "بيتنا في شبرا 1990")).accounts.length === 1;
      }, API, email, pw);
      check("النسخة التلقائية على السيرفر متشفرة وبترجع", ok);
      await p.browserContext().close();
    } else console.log("… اختبار السيرفر اتخطى (شغّله بـ NASEM_API=http://localhost:8000)");
  } finally { await b.close(); srv.close(); }
  console.log(`\n${pass} ✓   ${fail} ✗`);
  process.exit(fail ? 1 : 0);
})().catch(e => { console.error("FAIL", e); process.exit(1); });
