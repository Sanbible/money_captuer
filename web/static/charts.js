// กราฟใน dashboard (SVG/HTML ล้วน ไม่ใช้ library)
// - โดนัทแยกตามหมวด: สีตามลำดับ --series-1..6 (ผ่าน dataviz validator) เกิน 6 หมวดรวมเป็น "อื่น ๆ" ; legend มีชื่อ+ยอดทุกชิ้น
// - แท่งย้อนหลัง 6 เดือน: ชุดเดียว เน้นเดือนที่เลือกด้วยคอรัล ; ตัวเลขบนแท่ง + tooltip ยอดเต็ม
const MCCharts = (() => {
  const { el, baht } = MC;
  const NS = "http://www.w3.org/2000/svg";
  const SLOTS = 6;
  const SIZE = 180;
  const RADIUS = 70;
  const STROKE = 22;
  const GAP = 2; // ช่องว่างระหว่างชิ้น (px ตามเส้นรอบวง)

  function svg(tag, attrs = {}) {
    const node = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    return node;
  }

  function shortMonth(month) {
    const [y, m] = month.split("-").map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString("th-TH", { month: "short" });
  }

  function compact(n) {
    if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
    if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
    return String(Math.round(n));
  }

  // หมวดเรียงจากมากไปน้อยอยู่แล้ว (API) — เกิน 6 หมวด เก็บ 5 อันดับแรก ที่เหลือรวมเป็นชิ้นเดียว
  function fold(categories) {
    const items = categories.map((c) => ({ label: c.category, total: c.total }));
    if (items.length <= SLOTS) return items;
    const rest = items.slice(SLOTS - 1);
    return [...items.slice(0, SLOTS - 1),
            { label: `อื่น ๆ (${rest.length} หมวด)`, total: rest.reduce((a, c) => a + c.total, 0) }];
  }

  function donut(items, total) {
    const circumference = 2 * Math.PI * RADIUS;
    const root = svg("svg", { viewBox: `0 0 ${SIZE} ${SIZE}`, role: "img", "aria-label": "สัดส่วนรายจ่ายแยกตามหมวด" });
    let offset = 0;
    items.forEach((item, i) => {
      const length = (item.total / total) * circumference;
      const gap = items.length > 1 ? Math.min(GAP, length / 2) : 0;
      const seg = svg("circle", {
        class: "seg", cx: SIZE / 2, cy: SIZE / 2, r: RADIUS, fill: "none", "stroke-width": STROKE,
        "stroke-dasharray": `${Math.max(length - gap, 0)} ${circumference}`, "stroke-dashoffset": -offset,
        transform: `rotate(-90 ${SIZE / 2} ${SIZE / 2})`,
      });
      seg.style.stroke = `var(--series-${i + 1})`;
      const title = svg("title");
      title.textContent = `${item.label}: ${baht(item.total)} (${Math.round((item.total / total) * 100)}%)`;
      seg.append(title);
      root.append(seg);
      offset += length;
    });
    return el("div", { className: "donut-wrap" }, root,
      el("div", { className: "donut-center" },
        el("span", { className: "small", textContent: "รวม" }),
        el("strong", { textContent: baht(total) })));
  }

  function legend(items, total) {
    return items.map((item, i) => {
      const swatch = el("span", { className: "swatch" });
      swatch.style.background = `var(--series-${i + 1})`;
      const pct = Math.round((item.total / total) * 100);
      return el("li", {},
        swatch,
        el("div", { style: "min-width: 0" },
          el("div", { className: "name", textContent: `${item.label} · ${pct}%`, title: item.label }),
          el("div", { className: "value", textContent: baht(item.total) })));
    });
  }

  function bars(monthly, current) {
    const max = Math.max(...monthly.map((m) => m.total), 0);
    return monthly.map((m) => {
      const bar = el("div", { className: "bar", title: `${shortMonth(m.month)}: ${baht(m.total)}` });
      bar.style.height = max > 0 ? `${(m.total / max) * 100}%` : "0";
      return el("div", { className: `bar-col${m.month === current ? " is-current" : ""}` },
        el("span", { className: "bar-value", textContent: m.total > 0 ? `฿${compact(m.total)}` : "–" }),
        el("div", { style: "flex: 1; width: 100%; display: flex; align-items: flex-end; justify-content: center" }, bar),
        el("span", { className: "bar-label", textContent: shortMonth(m.month) }));
    });
  }

  function render(summary) {
    const empty = summary.by_category.length === 0;
    document.getElementById("chart-empty").classList.toggle("hidden", !empty);
    const items = fold(summary.by_category);
    const total = items.reduce((a, c) => a + c.total, 0);
    document.getElementById("donut").replaceChildren(...(empty ? [] : [donut(items, total)]));
    document.getElementById("legend").replaceChildren(...(empty ? [] : legend(items, total)));
    const barsBox = document.getElementById("bars");
    barsBox.replaceChildren(...bars(summary.monthly, summary.month));
    barsBox.setAttribute("aria-label", summary.monthly.map((m) => `${shortMonth(m.month)} ${baht(m.total)}`).join(", "));
  }

  return { render };
})();
