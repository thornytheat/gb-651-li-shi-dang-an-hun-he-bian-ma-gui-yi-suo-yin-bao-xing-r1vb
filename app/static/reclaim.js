// M7 安全导出与回收前端逻辑
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
));

async function refreshJobs() {
  const tbody = document.querySelector("#jobs-table tbody");
  if (!tbody) return;
  const res = await fetch("/api/reclaim/jobs");
  const data = await res.json();
  tbody.innerHTML = (data.jobs || []).map((j) => `
    <tr>
      <td>${esc(j.job_id)}</td>
      <td>${esc(j.run_id)}</td>
      <td><span class="sev sev--${esc(j.status).toLowerCase()}">${esc(j.status)}</span></td>
      <td>${esc(j.reason_code || "OK")}${j.reason_message ? " " + esc(j.reason_message) : ""}</td>
      <td>${esc(j.accepted_rows)}/${esc(j.row_count)}</td>
      <td>${j.status === "PENDING" || j.status === "PROCESSING" || j.status === "FAILED"
        ? `<button class="btn" data-resume="${esc(j.job_id)}">续跑</button>` : ""}</td>
    </tr>`).join("");
  tbody.querySelectorAll("[data-resume]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const r = await fetch(`/api/reclaim/jobs/${encodeURIComponent(btn.dataset.resume)}/resume`,
        { method: "POST" });
      document.querySelector("#reclaim-result").textContent =
        JSON.stringify(await r.json(), null, 2);
      refreshJobs();
    }));
}

document.querySelector("#safe-export")?.addEventListener("click", async (event) => {
  const out = document.querySelector("#export-result");
  const runId = event.target.dataset.runId || "";
  out.textContent = "导出中";
  try {
    const url = "/api/export/evidence.csv" + (runId ? `?run_id=${encodeURIComponent(runId)}` : "");
    const res = await fetch(url);
    if (!res.ok) {
      out.textContent = `导出失败：${res.status}`;
      return;
    }
    const blob = await res.blob();
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = res.headers.get("X-Evidence-Run-Id")
      ? `evidence-${res.headers.get("X-Evidence-Run-Id")}.csv` : "evidence.csv";
    a.click();
    URL.revokeObjectURL(a.href);
    out.textContent = "已导出（UTF-8 BOM，危险单元格带防护单引号，首行为清单）";
  } catch (e) {
    out.textContent = "请求失败，请重试";
  }
});

document.querySelector("#reclaim-btn")?.addEventListener("click", async () => {
  const input = document.querySelector("#reclaim-file");
  const out = document.querySelector("#reclaim-result");
  if (!input.files.length) {
    out.textContent = "请先选择回收的 CSV 文件";
    return;
  }
  out.textContent = "核验中";
  try {
    const text = await input.files[0].text();
    const res = await fetch("/api/reclaim", {
      method: "POST",
      headers: { "content-type": "text/csv; charset=utf-8" },
      body: text,
    });
    const data = await res.json();
    out.textContent = (res.ok ? "已接纳：" : `已拒收（${res.status}）：`)
      + JSON.stringify(data, null, 2);
    refreshJobs();
  } catch (e) {
    out.textContent = "请求失败，请重试";
  }
});

document.querySelector("#reload-jobs")?.addEventListener("click", refreshJobs);
refreshJobs();
