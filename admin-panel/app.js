/* ── Config ──────────────────────────────────────────────────────────────── */
const API = "";

/* ── API helper ─────────────────────────────────────────────────────────── */
async function api(path, opts = {}) {
  const method = opts.method || "GET";
  console.log(`[API] ${method} ${API + path}`, opts.body || "");
  const headers = { "Content-Type": "application/json" };
  try {
    const res = await fetch(API + path, {
      method,
      headers,
      credentials: "include",
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    console.log(`[API] Response ${res.status} for ${method} ${path}`);
    if (res.status === 401) {
      doLogout();
      throw new Error("Session expired");
    }
    if (!res.ok) {
      const e = await res.json().catch(() => ({ detail: res.statusText }));
      console.error(`[API] Error body:`, e);
      throw new Error(e.detail || "Request failed");
    }
    const ct = res.headers.get("content-type") || "";
    const result = ct.includes("json") ? await res.json() : await res.blob();
    console.log(`[API] Success for ${method} ${path}:`, result);
    return result;
  } catch (e) {
    console.error(`[API] Fetch failed for ${method} ${path}:`, e);
    throw e;
  }
}

function showMsg(id, text, ok = true) {
  const el = document.getElementById(id);
  if (!el) {
    console.warn(`[showMsg] Element #${id} not found`);
    return;
  }
  el.className = "msg " + (ok ? "ok" : "err");
  el.textContent = text;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 4000);
}

/* ── Auth ────────────────────────────────────────────────────────────────── */
async function doLogin() {
  const id = document.getElementById("login-id").value;
  const pw = document.getElementById("login-pass").value;
  console.log("[doLogin] Attempting login for id:", id);
  try {
    await api("/admin/login", {
      method: "POST",
      body: { id, password: pw },
    });
    console.log("[doLogin] Login successful");
    document.getElementById("login-screen").classList.add("hidden");
    document.getElementById("app").classList.remove("hidden");
    initApp();
  } catch (e) {
    console.error("[doLogin] Login failed:", e);
    const el = document.getElementById("login-error");
    el.textContent = e.message;
    el.classList.remove("hidden");
  }
}

async function doLogout() {
  console.log("[doLogout] Logging out");
  try { await api("/admin/logout", { method: "POST" }); } catch (_) {}
  document.getElementById("app").classList.add("hidden");
  document.getElementById("login-screen").classList.remove("hidden");
}

/* ── Navigation ─────────────────────────────────────────────────────────── */
function showPage(name) {
  console.log("[showPage]", name);
  document.querySelectorAll(".page").forEach((p) => p.classList.add("hidden"));
  document.getElementById("page-" + name).classList.remove("hidden");
  document
    .querySelectorAll(".nav-link")
    .forEach((a) => a.classList.remove("active"));
  document.querySelector(`[data-page="${name}"]`)?.classList.add("active");
  document.getElementById("sidebar").classList.remove("open");
  if (name === "logs") loadLogs("auth");
}

function toggleSidebar() {
  document.getElementById("sidebar").classList.toggle("open");
}

/* ── Init ────────────────────────────────────────────────────────────────── */
function initApp() {
  console.log("[initApp] Initialising app");
  loadCenters();
  loadShifts();
  renderUserFields();
  loadExamDropdowns();
}

// Auto-login via cookie
(async () => {
  try {
    await api("/admin/me");
    console.log("[boot] Session cookie valid, auto-logging in");
    document.getElementById("login-screen").classList.add("hidden");
    document.getElementById("app").classList.remove("hidden");
    initApp();
  } catch (_) {}
})();

/* ── Centers & Shifts ────────────────────────────────────────────────────── */
let CENTERS = [],
  SHIFTS = [],
  EXAMS = [];

async function loadCenters() {
  console.log("[loadCenters] Fetching centers");
  try {
    CENTERS = await api("/admin/centers");
    console.log("[loadCenters] Loaded", CENTERS.length, "centers:", CENTERS);
    const tbody = document.getElementById("center-list");
    tbody.innerHTML = CENTERS.map(
      (c) =>
        `<tr><td>${c.code}</td><td>${c.name || "—"}</td><td>${c.supervisor_name || "—"}</td><td>${c.vendor_name || "—"}</td></tr>`,
    ).join("");
    populateCenterDropdowns();
  } catch (e) {
    console.error("[loadCenters] Failed:", e);
  }
}

async function loadShifts() {
  console.log("[loadShifts] Fetching shifts");
  try {
    SHIFTS = await api("/admin/shifts");
    console.log("[loadShifts] Loaded", SHIFTS.length, "shifts:", SHIFTS);
    const tbody = document.getElementById("shift-list");
    tbody.innerHTML = SHIFTS.map(
      (s) =>
        `<tr><td>${s.shift_code || "—"}</td><td>${s.date}</td><td>${s.start_time}</td></tr>`,
    ).join("");
    populateShiftDropdowns();
  } catch (e) {
    console.error("[loadShifts] Failed:", e);
  }
}

async function createCenter() {
  const payload = {
    code: document.getElementById("c-code").value,
    name: document.getElementById("c-name").value || null,
    address: document.getElementById("c-addr").value || null,
    supervisor_name: document.getElementById("c-supname").value || null,
    vendor_name: document.getElementById("c-vendor").value || null,
  };
  console.log("[createCenter] Creating center:", payload);
  try {
    const result = await api("/admin/centers", {
      method: "POST",
      body: payload,
    });
    console.log("[createCenter] Created:", result);
    showMsg("msg-centers", "Center created");
    document.getElementById("c-code").value = "";
    document.getElementById("c-name").value = "";
    document.getElementById("c-addr").value = "";
    document.getElementById("c-supname").value = "";
    document.getElementById("c-vendor").value = "";
    loadCenters();
  } catch (e) {
    console.error("[createCenter] Failed:", e);
    showMsg("msg-centers", e.message, false);
  }
}

async function createShift() {
  const payload = {
    shift_code: document.getElementById("s-code").value || null,
    date: document.getElementById("s-date").value,
    start_time: document.getElementById("s-time").value,
  };
  console.log("[createShift] Creating shift:", payload);
  try {
    const result = await api("/admin/shifts", {
      method: "POST",
      body: payload,
    });
    console.log("[createShift] Created:", result);
    showMsg("msg-centers", "Shift created");
    document.getElementById("s-code").value = "";
    loadShifts();
  } catch (e) {
    console.error("[createShift] Failed:", e);
    showMsg("msg-centers", e.message, false);
  }
}

function populateCenterDropdowns() {
  console.log("[populateCenterDropdowns] Populating with", CENTERS.length, "centers");
  ["cand-center", "att-center", "match-center", "dup-center"].forEach((id) => {
    const sel = document.getElementById(id);
    if (!sel) return;
    const first = sel.options[0].outerHTML;
    sel.innerHTML =
      first +
      CENTERS.map(
        (c) => `<option value="${c.id}">${c.code}${c.name ? " — " + c.name : ""}</option>`,
      ).join("");
  });
}

function populateShiftDropdowns() {
  console.log("[populateShiftDropdowns] Populating with", SHIFTS.length, "shifts");
  ["cand-shift", "centers-shift-filter", "att-shift", "match-shift", "dup-shift"].forEach((id) => {
    const sel = document.getElementById(id);
    if (!sel) return;
    const first = sel.options[0].outerHTML;
    sel.innerHTML =
      first +
      SHIFTS.map(
        (s) =>
          `<option value="${s.id}">${s.shift_code ? s.shift_code + " – " : ""}${s.date} ${s.start_time}</option>`,
      ).join("");
  });
}

/* ── Exams ────────────────────────────────────────────────────────────────── */
async function loadExamDropdowns() {
  console.log("[loadExamDropdowns] Fetching exams");
  try {
    EXAMS = await api("/admin/exams");
    console.log("[loadExamDropdowns] Loaded", EXAMS.length, "exams:", EXAMS);
    ["cand-exam", "dash-exam"].forEach((id) => {
      const sel = document.getElementById(id);
      if (!sel) {
        console.warn("[loadExamDropdowns] Element not found:", id);
        return;
      }
      const first = sel.options[0].outerHTML;
      sel.innerHTML =
        first +
        EXAMS.map(
          (e) => `<option value="${e.id}">${e.name} (${e.type})</option>`,
        ).join("");
    });
    renderEditExams();
  } catch (e) {
    console.error("[loadExamDropdowns] Failed:", e);
  }
}

function renderUserFields() {
  const ns = parseInt(document.getElementById("ex-nsup").value) || 1;
  const no = parseInt(document.getElementById("ex-nop").value) || 1;
  console.log("[renderUserFields] Supervisors:", ns, "Operators:", no);
  document.getElementById("sup-fields").innerHTML =
    "<h3>Supervisors</h3>" +
    Array.from(
      { length: ns },
      (_, i) => `<div class="row">
      <input placeholder="Phone ${i + 1}" id="sup-ph-${i}">
      <input placeholder="Password ${i + 1}" id="sup-pw-${i}">
    </div>`,
    ).join("");
  document.getElementById("op-fields").innerHTML =
    "<h3>Operators</h3>" +
    Array.from(
      { length: no },
      (_, i) => `<div class="row">
      <input placeholder="Phone ${i + 1}" id="op-ph-${i}">
      <input placeholder="Password ${i + 1}" id="op-pw-${i}">
    </div>`,
    ).join("");
}

async function createExam() {
  const ns = parseInt(document.getElementById("ex-nsup").value);
  const no = parseInt(document.getElementById("ex-nop").value);
  const payload = {
    name: document.getElementById("ex-name").value,
    type: document.getElementById("ex-type").value,
    qr_string: document.getElementById("ex-qr").value || null,
    num_supervisors: ns,
    num_operators: no,
    supervisor_phones: Array.from(
      { length: ns },
      (_, i) => document.getElementById(`sup-ph-${i}`).value,
    ),
    supervisor_passwords: Array.from(
      { length: ns },
      (_, i) => document.getElementById(`sup-pw-${i}`).value,
    ),
    operator_phones: Array.from(
      { length: no },
      (_, i) => document.getElementById(`op-ph-${i}`).value,
    ),
    operator_passwords: Array.from(
      { length: no },
      (_, i) => document.getElementById(`op-pw-${i}`).value,
    ),
  };
  console.log("[createExam] Creating exam:", payload);
  try {
    const data = await api("/admin/exams", { method: "POST", body: payload });
    console.log("[createExam] Created:", data);
    showMsg("msg-exam", "Exam created!");
    const r = document.getElementById("exam-result");
    r.classList.remove("hidden");
    r.textContent = `ID: ${data.id}\nSync Key: ${data.sync_key}`;
    loadExamDropdowns();
  } catch (e) {
    console.error("[createExam] Failed:", e);
    showMsg("msg-exam", e.message, false);
  }
}

function renderEditExams() {
  console.log("[renderEditExams] Rendering", EXAMS.length, "exams");
  const el = document.getElementById("edit-exam-list");
  el.innerHTML = EXAMS.map(
    (e) => `
    <div class="card">
      <div class="row" style="align-items:center; justify-content:space-between">
        <div>
          <strong>${e.name}</strong>
          <small style="color:var(--muted)">${e.type} | ${e.id}</small>
        </div>
        <button onclick="toggleArchive('${e.id}', ${!e.archived})" class="${e.archived ? "" : "btn-red"}" style="flex:none">
          ${e.archived ? "Unarchive" : "Archive"}
        </button>
      </div>
      <div class="row" style="margin-top:.5rem">
        <input id="qr-${e.id}" value="${e.qr_string || ""}" placeholder="QR regex">
        <button onclick="updateQr('${e.id}')" style="flex:none; background:var(--sidebar-bg)">Save QR</button>
      </div>
      <div style="margin-top:.5rem">
        <label style="font-size:.8rem;color:var(--muted)">Last captured QR data (from device)</label>
        <textarea readonly rows="2" style="width:100%;resize:vertical;font-size:.8rem;margin-top:.25rem">${e.qr_data || ""}</textarea>
      </div>
    </div>
  `,
  ).join("");
}

async function toggleArchive(id, archived) {
  console.log("[toggleArchive] Exam:", id, "archived ->", archived);
  try {
    const result = await api(`/admin/exams/${id}`, {
      method: "PUT",
      body: { archived },
    });
    console.log("[toggleArchive] Updated:", result);
    loadExamDropdowns();
  } catch (e) {
    console.error("[toggleArchive] Failed:", e);
    alert(e.message);
  }
}

async function updateQr(id) {
  const qr = document.getElementById("qr-" + id).value;
  console.log("[updateQr] Exam:", id, "QR:", qr);
  try {
    const result = await api(`/admin/exams/${id}`, {
      method: "PUT",
      body: { qr_string: qr },
    });
    console.log("[updateQr] Updated:", result);
    alert("QR updated");
  } catch (e) {
    console.error("[updateQr] Failed:", e);
    alert(e.message);
  }
}

/* ── Add Candidates ──────────────────────────────────────────────────────── */
let csvHeaders = [],
  csvRows = [];
let photoMap = {};

document.getElementById("photo-folder") &&
  document
    .getElementById("photo-folder")
    .addEventListener("change", async function () {
      photoMap = {};
      const files = Array.from(this.files);
      console.log("[photo-folder] Selected", files.length, "files");
      if (!files.length) return;

      await Promise.all(
        files.map(
          (file) =>
            new Promise((resolve) => {
              const key = file.name
                .replace(/\.[^.]+$/, "")
                .trim()
                .toLowerCase();
              const reader = new FileReader();
              reader.onload = (e) => {
                photoMap[key] = e.target.result.split(",")[1];
                resolve();
              };
              reader.onerror = (err) => {
                console.warn(
                  "[photo-folder] Could not read file:",
                  file.name,
                  err,
                );
                resolve();
              };
              reader.readAsDataURL(file);
            }),
        ),
      );

      console.log(
        "[photo-folder] photoMap keys loaded:",
        Object.keys(photoMap),
      );
      const countEl = document.getElementById("photo-count");
      if (countEl)
        countEl.textContent = `✓ ${files.length} photo${files.length !== 1 ? "s" : ""} loaded from folder`;
    });

let fpMap = {},
  irisMap = {};

function _buildFileMap(files) {
  return new Promise((resolve) => {
    const map = {};
    if (!files.length) {
      resolve(map);
      return;
    }
    let pending = files.length;
    files.forEach((file) => {
      const key = file.name
        .replace(/\.[^.]+$/, "")
        .trim()
        .toLowerCase();
      const reader = new FileReader();
      reader.onload = (e) => {
        map[key] = e.target.result.split(",")[1];
        if (!--pending) resolve(map);
      };
      reader.onerror = () => {
        if (!--pending) resolve(map);
      };
      reader.readAsDataURL(file);
    });
  });
}

document.getElementById("fp-folder") &&
  document
    .getElementById("fp-folder")
    .addEventListener("change", async function () {
      fpMap = await _buildFileMap(Array.from(this.files));
      const el = document.getElementById("fp-count");
      if (el)
        el.textContent = `✓ ${Object.keys(fpMap).length} fingerprint image(s) loaded`;
      console.log("[fp-folder] fpMap keys:", Object.keys(fpMap).length);
    });

document.getElementById("iris-folder") &&
  document
    .getElementById("iris-folder")
    .addEventListener("change", async function () {
      irisMap = await _buildFileMap(Array.from(this.files));
      const el = document.getElementById("iris-count");
      if (el)
        el.textContent = `✓ ${Object.keys(irisMap).length} iris image(s) loaded`;
      console.log("[iris-folder] irisMap keys:", Object.keys(irisMap).length);
    });

function onCandExamChange() {
  const examId = document.getElementById("cand-exam").value;
  const exam = EXAMS.find((e) => e.id === examId);
  console.log("[onCandExamChange] Selected exam:", exam);
  const isMatch = exam && exam.type === "match";
  document
    .getElementById("fp-folder-wrap")
    .classList.toggle("hidden", !isMatch);
  document
    .getElementById("iris-folder-wrap")
    .classList.toggle("hidden", !isMatch);
  if (csvRows.length) renderMapping();
}

document.getElementById("cand-file").addEventListener("change", function () {
  const file = this.files[0];
  if (!file) {
    console.warn("[cand-file] No file selected");
    return;
  }
  console.log("[cand-file] Reading file:", file.name, "size:", file.size);
  const reader = new FileReader();
  reader.onload = function (e) {
    try {
      const wb = XLSX.read(new Uint8Array(e.target.result), {
        type: "array",
        cellDates: true,
      });
      console.log("[cand-file] Workbook sheets:", wb.SheetNames);
      const ws = wb.Sheets[wb.SheetNames[0]];
      const rows = XLSX.utils.sheet_to_json(ws, { header: 1, defval: "" });
      console.log(
        "[cand-file] Raw row count (including empties):",
        rows.length - 1,
      );
      if (rows.length < 2) {
        console.warn("[cand-file] File has no data rows");
        return;
      }
      csvHeaders = rows[0].map((h) => String(h).trim());
      console.log("[cand-file] Headers detected:", csvHeaders);
      csvRows = rows
        .slice(1)
        .filter((row) => row.some((cell) => String(cell).trim() !== ""))
        .map((row) => {
          const obj = {};
          csvHeaders.forEach((h, i) => (obj[h] = row[i] ?? ""));
          return obj;
        });
      console.log("[cand-file] Cleaned row count:", csvRows.length);
      console.log("[cand-file] First row sample:", csvRows[0]);
      renderMapping();
    } catch (err) {
      console.error("[cand-file] Failed to parse file:", err);
    }
  };
  reader.onerror = (err) => console.error("[cand-file] FileReader error:", err);
  reader.readAsArrayBuffer(file);
});

const CAND_FIELDS = [
  "center_code",
  "center_name",
  "center_address",
  "supervisor_name",
  "vendor_name",
  "shift_code",
  "shift_date",
  "shift_start_time",
  "candidate_no",
  "name",
  "roll_no",
  "father_name",
  "mother_name",
  "dob",
  "photo_filename",
  "fingerprint_filename",
  "iris_filename",
];

function renderMapping() {
  console.log(
    "[renderMapping] Rendering column map for",
    csvRows.length,
    "rows",
  );
  const examId = document.getElementById("cand-exam").value;
  const exam = EXAMS.find((e) => e.id === examId);
  const isMatch = exam && exam.type === "match";
  const el = document.getElementById("cand-mapping");
  el.classList.remove("hidden");
  el.innerHTML =
    '<h3>Map Columns</h3><div class="grid-2">' +
    CAND_FIELDS.map((f) => {
      const isBioField = f === "fingerprint_filename" || f === "iris_filename";
      const disabled =
        (document.getElementById("cand-center").value &&
          f.startsWith("center")) ||
        (document.getElementById("cand-shift").value &&
          f.startsWith("shift")) ||
        (isBioField && !isMatch);
      return `<div><label>${f.replace(/_/g, " ")}</label>
      <select id="map-${f}" ${disabled ? "disabled" : ""}>
        <option value="">— skip —</option>
        ${csvHeaders.map((h) => `<option value="${h}" ${h.toLowerCase().replace(/[^a-z]/g, "") === f.replace(/_/g, "") ? "selected" : ""}>${h}</option>`).join("")}
      </select></div>`;
    }).join("") +
    '</div><p style="color:var(--muted);font-size:.85rem;margin-top:.5rem">' +
    csvRows.length +
    " rows detected</p>";
  document.getElementById("cand-upload-btn").classList.remove("hidden");
  console.log("[renderMapping] Done. Upload button shown.");
}

/* ── Date/time parsing helpers ──────────────────────────────────────────── */

// XLSX 0.15.1 with cellDates:true returns LOCAL-time Date objects.
// All helpers below create and read Dates in LOCAL time so results are
// correct regardless of the browser's timezone.

function parseExcelCell(val) {
  if (val instanceof Date) return val;
  if (val === null || val === undefined || val === "") return null;

  if (typeof val === "number" || /^\d+(\.\d+)?$/.test(String(val))) {
    const num = Number(val);
    if (num > 10000) {
      // Excel date serial: days since 1899-12-30 LOCAL midnight.
      // Use local-time epoch so getFullYear/getMonth/getDate stay correct.
      const excelEpoch = new Date(1899, 11, 30, 0, 0, 0, 0);
      return new Date(excelEpoch.getTime() + Math.round(num * 86400000));
    }
    return null;
  }

  const v = String(val).trim();
  // Use Date(y, m, d) constructor — always LOCAL midnight, never UTC.
  const fmts = [
    { re: /^(\d{4})-(\d{2})-(\d{2})$/, yi: 1, mi: 2, di: 3 }, // YYYY-MM-DD
    { re: /^(\d{2})\/(\d{2})\/(\d{4})$/, yi: 3, mi: 2, di: 1 }, // DD/MM/YYYY
    { re: /^(\d{2})-(\d{2})-(\d{4})$/, yi: 3, mi: 2, di: 1 }, // DD-MM-YYYY
  ];
  for (const { re, yi, mi, di } of fmts) {
    const match = v.match(re);
    if (match) {
      const dt = new Date(
        Number(match[yi]),
        Number(match[mi]) - 1,
        Number(match[di]),
      );
      if (!isNaN(dt)) return dt;
    }
  }
  // Last-resort fallback — re-express as local midnight to avoid UTC-offset issues
  const fallback = new Date(v);
  if (isNaN(fallback)) return null;
  return new Date(
    fallback.getFullYear(),
    fallback.getMonth(),
    fallback.getDate(),
  );
}

function toSQLDate(value) {
  if (!value && value !== 0) return null;
  const d = parseExcelCell(value);
  if (!d) return null;
  // Read LOCAL date components — d is always a local-time Date
  const y = d.getFullYear();
  const mo = String(d.getMonth() + 1).padStart(2, "0");
  const dy = String(d.getDate()).padStart(2, "0");
  return `${y}-${mo}-${dy}`;
}

function _hms(h, m, s) {
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function toSQLTime(value) {
  if (!value && value !== 0) return null;

  // JS Date object (XLSX cellDates:true) — read local-time components directly
  if (value instanceof Date) {
    return _hms(value.getHours(), value.getMinutes(), value.getSeconds());
  }

  // Excel time fraction: 0 <= n < 1 represents 00:00:00–23:59:59
  const num = Number(value);
  if (!isNaN(num) && num >= 0 && num < 1) {
    const s = Math.round(num * 86400);
    return _hms(Math.floor(s / 3600) % 24, Math.floor(s / 60) % 60, s % 60);
  }

  // Any string — prepend a fixed date and let the browser parse it as local time.
  // Handles 24h ("14:30", "09:00:00"), 12h ("10:00 AM", "2:00 PM"), and anything
  // else the browser's date parser understands.
  const v = String(value).trim();
  if (!v) return null;
  const d = new Date("1970-01-01 " + v);
  if (!isNaN(d)) return _hms(d.getHours(), d.getMinutes(), d.getSeconds());
  return null;
}

async function uploadCandidates() {
  console.log("[uploadCandidates] Called");

  const examId = document.getElementById("cand-exam").value;
  console.log(
    "[uploadCandidates] examId:",
    examId,
    "| csvRows:",
    csvRows.length,
  );

  if (!examId) {
    console.warn("[uploadCandidates] Aborted: no exam selected");
    showMsg("msg-cand", "Please select an exam first.", false);
    return;
  }
  if (!csvRows.length) {
    console.warn("[uploadCandidates] Aborted: no CSV rows loaded");
    showMsg(
      "msg-cand",
      "No candidate rows found. Please upload a file first.",
      false,
    );
    return;
  }

  const centerId = document.getElementById("cand-center").value;
  const shiftId = document.getElementById("cand-shift").value;
  const center = CENTERS.find((c) => c.id === centerId);
  const shift = SHIFTS.find((s) => s.id === shiftId);
  const examId2 = document.getElementById("cand-exam").value;
  const exam = EXAMS.find((e) => e.id === examId2);
  const isMatch = exam && exam.type === "match";

  console.log(
    "[uploadCandidates] centerId:",
    centerId,
    "| center object:",
    center,
  );
  console.log("[uploadCandidates] shiftId:", shiftId, "| shift object:", shift);
  console.log("[uploadCandidates] isMatch:", isMatch);

  // Build column map from dropdowns
  const map = {};
  CAND_FIELDS.forEach((f) => {
    const el = document.getElementById("map-" + f);
    map[f] = el ? el.value : "";
  });
  console.log("[uploadCandidates] Column map:", map);
  console.log(
    "[uploadCandidates] photoMap:",
    Object.keys(photoMap).length,
    "fpMap:",
    Object.keys(fpMap).length,
    "irisMap:",
    Object.keys(irisMap).length,
  );

  const btn = document.getElementById("cand-upload-btn");
  btn.disabled = true;
  btn.textContent = "Uploading…";

  let ok = 0,
    errors = [];

  for (let i = 0; i < csvRows.length; i++) {
    const row = csvRows[i];

    const candidateNo = String(row[map.candidate_no] || "").trim();
    const rollNo = row[map.roll_no] ? String(row[map.roll_no]).trim() : null;
    const name = String(row[map.name] || "").trim();

    // Resolve photo via mapped photo_filename column
    let photo = null;
    const rawFilename = map.photo_filename
      ? String(row[map.photo_filename] || "").trim()
      : "";
    if (rawFilename) {
      const lookupKey = rawFilename
        .replace(/\.[^.]+$/, "")
        .trim()
        .toLowerCase();
      photo = photoMap[lookupKey] || null;
      if (!photo) {
        console.warn(
          `[uploadCandidates] Row ${i + 2}: photo not found for key "${lookupKey}"`,
        );
      }
    }

    const body = {
      exam_id: examId,
      center_code: centerId
        ? center.code
        : String(row[map.center_code] || "").trim(),
      center_name: centerId
        ? center.name
        : String(row[map.center_name] || "").trim(),
      center_address: centerId
        ? center.address
        : String(row[map.center_address] || "").trim(),
      supervisor_name: map.supervisor_name
        ? String(row[map.supervisor_name] || "").trim() || null
        : null,
      vendor_name: map.vendor_name
        ? String(row[map.vendor_name] || "").trim() || null
        : null,
      shift_code: shiftId
        ? shift.shift_code
        : String(row[map.shift_code] || "").trim(),
      shift_date: shiftId
        ? toSQLDate(shift.date)
        : toSQLDate(row[map.shift_date]),
      shift_start_time: shiftId
        ? toSQLTime(shift.start_time)
        : toSQLTime(row[map.shift_start_time]),
      candidate_no: candidateNo,
      name,
      roll_no: rollNo,
      father_name: row[map.father_name]
        ? String(row[map.father_name]).trim()
        : null,
      mother_name: row[map.mother_name]
        ? String(row[map.mother_name]).trim()
        : null,
      dob: toSQLDate(row[map.dob]) || null,
      photo_base64: photo,
    };

    if (isMatch) {
      const fpFilename = map.fingerprint_filename
        ? String(row[map.fingerprint_filename] || "").trim()
        : "";
      if (fpFilename) {
        const key = fpFilename
          .replace(/\.[^.]+$/, "")
          .trim()
          .toLowerCase();
        body.fingerprint_base64 = fpMap[key] || null;
      }
      const irisFilename = map.iris_filename
        ? String(row[map.iris_filename] || "").trim()
        : "";
      if (irisFilename) {
        const key = irisFilename
          .replace(/\.[^.]+$/, "")
          .trim()
          .toLowerCase();
        body.iris_base64 = irisMap[key] || null;
      }
    }

    console.log(`[uploadCandidates] Row ${i + 2} body:`, {
      ...body,
      photo_base64: photo
        ? `[base64 ~${Math.round(photo.length / 1024)}KB]`
        : null,
    });
    try {
      await api("/admin/candidates", { method: "POST", body });
      ok++;
    } catch (e) {
      console.error(`[uploadCandidates] Row ${i + 2} failed:`, e);
      errors.push(`Row ${i + 2}: ${e.message}`);
    }

    if ((i + 1) % 10 === 0 || i === csvRows.length - 1) {
      btn.textContent = `Uploading… ${i + 1}/${csvRows.length}`;
    }
  }

  btn.disabled = false;
  btn.textContent = "Upload Candidates";

  console.log(
    `[uploadCandidates] Done. OK: ${ok}, Errors: ${errors.length}`,
    errors,
  );
  const msg =
    `${ok} added.` +
    (errors.length ? ` ${errors.length} errors:\n` + errors.join("\n") : "");
  showMsg("msg-cand", msg, errors.length === 0);
}

/* ── Dashboard (tabbed) ──────────────────────────────────────────────────── */
let currentDashTab = "all-centers";
let autoRefreshTimer = null;

function onDashExamChange() {
  console.log("[onDashExamChange] exam changed");
  loadDashTab();
}

function switchDashTab(tab) {
  console.log("[switchDashTab]", tab);
  currentDashTab = tab;
  document
    .querySelectorAll("#page-dashboard .tab")
    .forEach((t) => t.classList.toggle("active", t.dataset.tab === tab));
  ["all-centers", "matching", "duplicates"].forEach((t) => {
    const el = document.getElementById("dash-tab-" + t);
    if (el) el.classList.toggle("hidden", t !== tab);
  });
  loadDashTab();
}

async function loadDashTab() {
  const examId = document.getElementById("dash-exam").value;
  if (!examId) return;
  if (currentDashTab === "all-centers") {
    await loadCentersSummary();
  } else if (currentDashTab === "matching") {
    await loadMatchingTab(examId);
  } else if (currentDashTab === "duplicates") {
    await loadDuplicatesTab(examId);
  }
}

function drillToMatching(centerId, shiftId) {
  const cSel = document.getElementById("match-center");
  const sSel = document.getElementById("match-shift");
  if (cSel) cSel.value = centerId;
  if (sSel) sSel.value = shiftId;
  switchDashTab("matching");
}

function drillToDuplicates(centerId, shiftId) {
  const cSel = document.getElementById("dup-center");
  const sSel = document.getElementById("dup-shift");
  if (cSel) cSel.value = centerId;
  if (sSel) sSel.value = shiftId;
  switchDashTab("duplicates");
}

function toggleAutoRefresh() {
  const on = document.getElementById("auto-refresh").checked;
  if (on) {
    autoRefreshTimer = setInterval(loadCentersSummary, 30000);
  } else {
    clearInterval(autoRefreshTimer);
    autoRefreshTimer = null;
  }
}

async function loadCentersSummary() {
  const examId = document.getElementById("dash-exam").value;
  if (!examId) return;
  const sid = document.getElementById("centers-shift-filter").value;
  let url = `/admin/centers-summary?exam_id=${examId}`;
  if (sid) url += `&shift_id=${sid}`;
  console.log("[loadCentersSummary] URL:", url);
  try {
    const data = await api(url);
    console.log("[loadCentersSummary] Received", data.length, "rows");

    const exam = EXAMS.find((e) => e.id === examId);
    const isMatch = exam && exam.type === "match";

    // Dynamic column headers
    const headers = isMatch
      ? ["Center", "Code", "Shift", "Total", "Present", "Absent",
         "Photo ✓", "Photo ✗", "FP ✓", "FP ✗", "Iris ✓", "Iris ✗",
         "Devices", "Supervisor", "Vendor"]
      : ["Center", "Code", "Shift", "Total", "Present", "Absent",
         "Matched", "Mismatched", "Duplicates", "Devices", "Supervisor", "Vendor"];
    document.getElementById("ac-thead").innerHTML =
      "<tr>" + headers.map((h) => `<th>${h}</th>`).join("") + "</tr>";

    // Body rows
    document.getElementById("centers-summary-body").innerHTML = data
      .map((r) => {
        const midCells = isMatch
          ? `<td>${r.matched}</td><td>${r.mismatched}</td>
             <td>${r.fp_matched}</td><td>${r.fp_mismatched}</td>
             <td>${r.iris_matched}</td><td>${r.iris_mismatched}</td>`
          : `<td><a href="#" onclick="drillToMatching('${r.center_id}','${r.shift_id}');return false">${r.matched}</a></td>
             <td><a href="#" onclick="drillToMatching('${r.center_id}','${r.shift_id}');return false">${r.mismatched}</a></td>
             <td><a href="#" onclick="drillToDuplicates('${r.center_id}','${r.shift_id}');return false">${r.duplicates}</a></td>`;
        return `<tr>
          <td><a href="#" onclick="drillToMatching('${r.center_id}','${r.shift_id}');return false">${r.center_name || r.center_code}</a></td>
          <td>${r.center_code}</td>
          <td>${r.shift_code || "—"}</td>
          <td>${r.total}</td>
          <td>${r.present}</td>
          <td>${r.absent}</td>
          ${midCells}
          <td>${r.active_devices}</td>
          <td>${r.supervisor_name || "—"}</td>
          <td>${r.vendor_name || "—"}</td>
        </tr>`;
      })
      .join("");

    // Totals row
    const tot = data.reduce((s, r) => s + r.total, 0);
    const pres = data.reduce((s, r) => s + r.present, 0);
    const abs = data.reduce((s, r) => s + r.absent, 0);
    const devs = data.reduce((s, r) => s + r.active_devices, 0);
    let totalsHtml;
    if (isMatch) {
      const photoM = data.reduce((s, r) => s + r.matched, 0);
      const photoX = data.reduce((s, r) => s + r.mismatched, 0);
      const fpM = data.reduce((s, r) => s + r.fp_matched, 0);
      const fpX = data.reduce((s, r) => s + r.fp_mismatched, 0);
      const irisM = data.reduce((s, r) => s + r.iris_matched, 0);
      const irisX = data.reduce((s, r) => s + r.iris_mismatched, 0);
      totalsHtml = `<tr><td colspan="3"><strong>TOTAL</strong></td>
        <td><strong>${tot}</strong></td><td><strong>${pres}</strong></td><td><strong>${abs}</strong></td>
        <td><strong>${photoM}</strong></td><td><strong>${photoX}</strong></td>
        <td><strong>${fpM}</strong></td><td><strong>${fpX}</strong></td>
        <td><strong>${irisM}</strong></td><td><strong>${irisX}</strong></td>
        <td><strong>${devs}</strong></td><td colspan="2"></td></tr>`;
    } else {
      const matched = data.reduce((s, r) => s + r.matched, 0);
      const mismatched = data.reduce((s, r) => s + r.mismatched, 0);
      const dups = data.reduce((s, r) => s + r.duplicates, 0);
      totalsHtml = `<tr><td colspan="3"><strong>TOTAL</strong></td>
        <td><strong>${tot}</strong></td><td><strong>${pres}</strong></td><td><strong>${abs}</strong></td>
        <td><strong>${matched}</strong></td><td><strong>${mismatched}</strong></td><td><strong>${dups}</strong></td>
        <td><strong>${devs}</strong></td><td colspan="2"></td></tr>`;
    }
    document.getElementById("ac-tfoot").innerHTML = totalsHtml;

    // Stat cards
    document.getElementById("ac-total").textContent = tot;
    document.getElementById("ac-present").textContent = pres;
    document.getElementById("ac-absent").textContent = abs;
    document.getElementById("ac-stats").classList.remove("hidden");
    document.getElementById("ac-charts").classList.remove("hidden");

    // Charts
    drawPie("ac-chart-att", [
      { label: "Present", value: pres, color: "#10b981" },
      { label: "Absent", value: abs, color: "#ef4444" },
    ]);
    const photoMatch = data.reduce((s, r) => s + r.matched, 0);
    const photoMism = data.reduce((s, r) => s + r.mismatched, 0);
    drawPie("ac-chart-photo", [
      { label: "Matched", value: photoMatch, color: "#10b981" },
      { label: "Mismatch", value: photoMism, color: "#ef4444" },
      { label: "Pending", value: tot - photoMatch - photoMism, color: "#f59e0b" },
    ]);
    if (isMatch) {
      const fpM = data.reduce((s, r) => s + r.fp_matched, 0);
      const fpX = data.reduce((s, r) => s + r.fp_mismatched, 0);
      const irisM = data.reduce((s, r) => s + r.iris_matched, 0);
      const irisX = data.reduce((s, r) => s + r.iris_mismatched, 0);
      document.getElementById("ac-chart-fp-wrap").classList.remove("hidden");
      document.getElementById("ac-chart-iris-wrap").classList.remove("hidden");
      drawPie("ac-chart-fp", [
        { label: "FP Match", value: fpM, color: "#10b981" },
        { label: "FP Mismatch", value: fpX, color: "#ef4444" },
        { label: "Pending", value: tot - fpM - fpX, color: "#f59e0b" },
      ]);
      drawPie("ac-chart-iris", [
        { label: "Iris Match", value: irisM, color: "#10b981" },
        { label: "Iris Mismatch", value: irisX, color: "#ef4444" },
        { label: "Pending", value: tot - irisM - irisX, color: "#f59e0b" },
      ]);
    } else {
      document.getElementById("ac-chart-fp-wrap").classList.add("hidden");
      document.getElementById("ac-chart-iris-wrap").classList.add("hidden");
    }
  } catch (e) {
    console.error("[loadCentersSummary] Failed:", e);
  }
}

function _thumb(b64, mime) {
  if (!b64) return "—";

  // fallback if mime not provided
  const type = mime || "image/jpeg";

  return `<img src="data:${type};base64,${b64}" 
    style="height:40px;border-radius:3px;cursor:pointer"
    onclick="window.open('data:${type};base64,${b64}')">`;
}

async function loadMatchingTab(examId) {
  const cid = document.getElementById("match-center").value;
  const sid = document.getElementById("match-shift").value;
  const promptEl = document.getElementById("match-prompt");
  const contentEl = document.getElementById("match-content");
  if (!cid || !sid) {
    promptEl.classList.remove("hidden");
    contentEl.classList.add("hidden");
    return;
  }
  promptEl.classList.add("hidden");
  contentEl.classList.remove("hidden");
  const url = `/admin/matching?exam_id=${examId}&center_id=${cid}&shift_id=${sid}`;
  console.log("[loadMatchingTab] URL:", url);
  try {
    const data = await api(url);
    console.log("[loadMatchingTab] Received", data.length, "records");
    const matched = data.filter((d) => d.photo_match_status === "match").length;
    const failed = data.filter(
      (d) => d.photo_match_status === "mismatch",
    ).length;
    const pending = data.length - matched - failed;
    document.getElementById("match-matched").textContent = matched;
    document.getElementById("match-failed").textContent = failed;
    document.getElementById("match-pending").textContent = pending;
    drawPie("match-chart", [
      { label: "Matched", value: matched, color: "#10b981" },
      { label: "Failed", value: failed, color: "#ef4444" },
      { label: "Pending", value: pending, color: "#f59e0b" },
    ]);
    const exam = EXAMS.find((e) => e.id === examId);
    const isMatch = exam && exam.type === "match";
    document.querySelector("#dash-tab-matching table thead tr").innerHTML =
      isMatch
        ? "<th>No</th><th>Name</th><th>Attended</th><th>Ref Photo</th><th>Captured</th><th>Photo</th><th>Ref FP</th><th>Live FP</th><th>FP Match</th><th>Ref Iris</th><th>Live Iris</th><th>Iris Match</th><th>Center</th><th>Shift</th>"
        : "<th>No</th><th>Name</th><th>Attended</th><th>Ref Photo</th><th>Captured</th><th>Photo</th><th>Fingerprint</th><th>Iris</th><th>Center</th><th>Shift</th>";
    const mismatches = data.filter((d) =>
      d.photo_match_status === "mismatch" ||
      (isMatch && (d.fingerprint_match_status === "mismatch" || d.iris_match_status === "mismatch"))
    );
    document.getElementById("match-body").innerHTML = mismatches
      .map(
        (d, i) => `<tr>
      <td>${i + 1}</td><td>${d.name}</td>
      <td><span class="badge ${d.attended ? "badge-yes" : "badge-no"}">${d.attended ? "Yes" : "No"}</span></td>
      <td>${_thumb(d.photo_data)}</td>
      <td>${_thumb(d.captured_photo_data)}</td>
      <td>${statusBadge(d.photo_match_status)}</td>
      ${
        isMatch
          ? `
        <td>${_thumb(d.fingerprint_data)}</td>
        <td>${_thumb(d.live_fingerprint_data)}</td>
        <td>${statusBadge(d.fingerprint_match_status)}</td>
        <td>${_thumb(d.iris_data)}</td>
        <td>${_thumb(d.live_iris_data)}</td>
        <td>${statusBadge(d.iris_match_status)}</td>
      `
          : `
        <td>${_thumb(d.fingerprint_data)}</td>
        <td>${_thumb(d.iris_data)}</td>
      `
      }
      <td>${d.center_code}</td>
      <td>${d.shift_code || "—"}</td>
    </tr>`,
      )
      .join("");
  } catch (e) {
    console.error("[loadMatchingTab] Failed:", e);
  }
}

async function loadDuplicatesTab(examId) {
  const cid = document.getElementById("dup-center").value;
  const sid = document.getElementById("dup-shift").value;
  const promptEl = document.getElementById("dup-prompt");
  if (!cid || !sid) {
    promptEl.classList.remove("hidden");
    return;
  }
  promptEl.classList.add("hidden");
  const url = `/admin/matching/duplicates?exam_id=${examId}&center_id=${cid}&shift_id=${sid}`;
  console.log("[loadDuplicatesTab] URL:", url);
  const emptyEl = document.getElementById("dup-empty");
  const listEl = document.getElementById("dup-list");
  try {
    const rows = await api(url);
    console.log(
      "[loadDuplicatesTab] Received",
      rows.length,
      "duplicate submission rows",
    );
    if (!rows.length) {
      emptyEl.style.display = "";
      listEl.innerHTML = "";
      return;
    }
    emptyEl.style.display = "none";

    // Group rows by candidate_no
    const groups = {};
    rows.forEach((r) => {
      if (!groups[r.candidate_no]) groups[r.candidate_no] = [];
      groups[r.candidate_no].push(r);
    });

    listEl.innerHTML = Object.values(groups)
      .map(
        (g) => `
      <div class="card" style="margin-bottom:1rem">
        <p><strong>${g[0].name}</strong> (${g[0].candidate_no}) — ${g.length} captures</p>
        <table style="width:100%"><thead><tr>
          <th>Ref Photo</th><th>Captured Photo</th><th>Photo Match</th>
          <th>Fingerprint</th><th>Iris</th><th>Attended</th><th>Actions</th>
        </tr></thead><tbody>
        ${g
          .map(
            (r) => `<tr>
          <td>${_thumb(r.photo_data)}</td>
          <td>${_thumb(r.captured_photo_data)}</td>
          <td>${statusBadge(r.photo_match_status)}</td>
          <td>${_thumb(r.fingerprint_data)}</td>
          <td>${_thumb(r.iris_data)}</td>
          <td><span class="badge ${r.attended ? "badge-yes" : "badge-no"}">${r.attended ? "Yes" : "No"}</span></td>
          <td>
            <button onclick="keepCapture('${r.capture_id}')" style="background:var(--sidebar-bg);margin-right:.25rem">Keep Only This</button>
            <button onclick="removeCapture('${r.capture_id}')" class="btn-red">Remove</button>
          </td>
        </tr>`,
          )
          .join("")}
        </tbody></table>
      </div>
    `,
      )
      .join("");
  } catch (e) {
    console.error("[loadDuplicatesTab] Failed:", e);
  }
}

async function keepCapture(keepCaptureId) {
  console.log("[keepCapture] keepCaptureId:", keepCaptureId);
  if (
    !confirm(
      "Keep this capture and delete all other submissions for this candidate?",
    )
  )
    return;
  try {
    await api("/admin/matching/resolve-duplicate", {
      method: "POST",
      body: { keep_capture_id: keepCaptureId },
    });
    alert("Duplicate resolved.");
    loadDashTab();
  } catch (e) {
    console.error("[keepCapture] Failed:", e);
    alert("Error: " + e.message);
  }
}

async function removeCapture(captureId) {
  console.log("[removeCapture] captureId:", captureId);
  if (!confirm("Remove this capture submission? This cannot be undone."))
    return;
  try {
    await api(`/admin/matching1/${captureId}`, { method: "DELETE" });
    alert("Submission removed.");
    loadDashTab();
  } catch (e) {
    console.error("[removeCapture] Failed:", e);
    alert("Error: " + e.message);
  }
}

function statusBadge(val) {
  if (val == null || val === "" || val === "pending") {
    return val === "pending"
      ? '<span class="badge badge-pending">Pending</span>'
      : "—";
  }
  if (val === "match") return '<span class="badge badge-yes">Match</span>';
  if (val === "mismatch") return '<span class="badge badge-no">Mismatch</span>';
  return `<span class="badge badge-pending">${val}</span>`;
}

async function downloadMatching(fmt) {
  const examId = document.getElementById("dash-exam").value;
  if (!examId) return;
  const cid = document.getElementById("match-center").value;
  const sid = document.getElementById("match-shift").value;
  console.log("[downloadMatching] format:", fmt, "examId:", examId);
  let url = `/admin/matching/download?exam_id=${examId}&format=${fmt}`;
  if (cid) url += `&center_id=${cid}`;
  if (sid) url += `&shift_id=${sid}`;
  try {
    const blob = await api(url);
    downloadBlob(blob, "matching." + fmt);
  } catch (e) {
    console.error("[downloadMatching] Failed:", e);
  }
}

/* ── Logs ────────────────────────────────────────────────────────────────── */
async function loadLogs(type) {
  console.log("[loadLogs] type:", type);
  document
    .querySelectorAll("#page-logs .tab")
    .forEach((t) => t.classList.toggle("active", t.dataset.tab === type));
  try {
    const data = await api(`/admin/logs/${type}`);
    console.log("[loadLogs] Received", data.length, "log entries");
    if (type === "auth") {
      document.getElementById("log-head").innerHTML =
        "<tr><th>Photo</th><th>Mobile</th><th>Role</th><th>Name</th><th>Time</th></tr>";
      document.getElementById("log-body").innerHTML = data
        .map(
          (d) => `<tr>
        <td>${d.photo_data ? `<img src="data:image/jpeg;base64,${d.photo_data}" style="height:40px;border-radius:3px">` : "—"}</td>
        <td>${d.mobile || "—"}</td><td>${d.role || "—"}</td>
        <td>${d.name || "—"}</td><td>${d.created_at || ""}</td>
      </tr>`,
        )
        .join("");
    } else {
      document.getElementById("log-head").innerHTML =
        "<tr><th>Action</th><th>Details</th><th>Time</th></tr>";
      document.getElementById("log-body").innerHTML = data
        .map(
          (d) => `<tr>
        <td>${d.action}</td>
        <td style="max-width:300px;overflow:hidden;text-overflow:ellipsis">${d.details || "—"}</td>
        <td>${d.timestamp || d.created_at || ""}</td>
      </tr>`,
        )
        .join("");
    }
  } catch (e) {
    console.error("[loadLogs] Failed:", e);
  }
}

/* ── Pie chart (canvas, no library) ──────────────────────────────────────── */
function drawPie(canvasId, slices) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) {
    console.warn("[drawPie] Canvas not found:", canvasId);
    return;
  }
  const ctx = canvas.getContext("2d");
  const w = (canvas.width = canvas.offsetWidth);
  const h = (canvas.height = 220);
  ctx.clearRect(0, 0, w, h);

  const total = slices.reduce((a, s) => a + s.value, 0);
  console.log("[drawPie]", canvasId, "| total:", total, "| slices:", slices);
  if (total === 0) return;

  const cx = w / 2 - 60,
    cy = h / 2,
    r = 80,
    ir = 45;
  let angle = -Math.PI / 2;

  slices.forEach((s) => {
    const sliceAngle = (s.value / total) * Math.PI * 2;
    ctx.beginPath();
    ctx.moveTo(cx + Math.cos(angle) * ir, cy + Math.sin(angle) * ir);
    ctx.arc(cx, cy, r, angle, angle + sliceAngle);
    ctx.arc(cx, cy, ir, angle + sliceAngle, angle, true);
    ctx.closePath();
    ctx.fillStyle = s.color;
    ctx.fill();
    angle += sliceAngle;
  });

  let ly = 30;
  slices.forEach((s) => {
    ctx.fillStyle = s.color;
    ctx.fillRect(w - 120, ly, 14, 14);
    ctx.fillStyle = "#334155";
    ctx.font = "13px sans-serif";
    ctx.fillText(`${s.label}: ${s.value}`, w - 100, ly + 12);
    ly += 24;
  });
}

/* ── Download blob ──────────────────────────────────────────────────────── */
function downloadBlob(blob, filename) {
  console.log("[downloadBlob] filename:", filename, "size:", blob.size);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}
