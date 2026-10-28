// กราฟใน dashboard — ข้อมูลชุดเดียวต่อกราฟ จึงใช้สีเดียว (--series-1) ; ตารางรายการคือ text view ของกราฟ
const MCCharts = (() => {
  const charts = {};
  const BAR_ROW_PX = 34; // ความสูงต่อหมวดของกราฟแท่งแนวนอน

  function token(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }

  function shortMonth(month) {
    const [y, m] = month.split("-").map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString("th-TH", { month: "short" });
  }

  function baseOptions(indexAxis) {
    const ink = token("--muted");
    const grid = token("--line");
    const valueAxis = indexAxis === "y" ? "x" : "y";
    return {
      indexAxis,
      maintainAspectRatio: false,
      animation: { duration: 250 },
      plugins: {
        legend: { display: false }, // ชุดเดียว ชื่อกราฟบอกอยู่แล้ว
        tooltip: { callbacks: { label: (ctx) => ` ${MC.baht(ctx.parsed[valueAxis])}` } },
      },
      scales: {
        [valueAxis]: {
          beginAtZero: true,
          grid: { color: grid },
          border: { display: false },
          ticks: { color: ink, maxTicksLimit: 5, callback: (v) => `฿${Number(v).toLocaleString("th-TH")}` },
        },
        [indexAxis]: { grid: { display: false }, border: { color: grid }, ticks: { color: ink } },
      },
    };
  }

  function draw(id, config) {
    charts[id]?.destroy(); // กันกราฟซ้อนเมื่อโหลดเดือนใหม่
    charts[id] = new Chart(document.getElementById(id), config);
  }

  function bars(labels, values) {
    return {
      labels,
      datasets: [{
        data: values,
        backgroundColor: token("--series-1"),
        borderRadius: 4,
        borderSkipped: "start", // มนเฉพาะปลายแท่ง ฐานติดแกน
        maxBarThickness: 28,
      }],
    };
  }

  function render(summary) {
    const empty = summary.by_category.length === 0;
    document.getElementById("chart-empty").classList.toggle("hidden", !empty);
    const categoryCanvas = document.getElementById("chart-category");
    categoryCanvas.parentElement.classList.toggle("hidden", empty);
    if (empty) {
      charts["chart-category"]?.destroy();
      delete charts["chart-category"];
    } else {
      categoryCanvas.parentElement.style.height = `${Math.max(160, summary.by_category.length * BAR_ROW_PX + 40)}px`;
      draw("chart-category", {
        type: "bar",
        data: bars(summary.by_category.map((c) => c.category), summary.by_category.map((c) => c.total)),
        options: baseOptions("y"),
      });
    }

    draw("chart-monthly", {
      type: "bar",
      data: bars(summary.monthly.map((m) => shortMonth(m.month)), summary.monthly.map((m) => m.total)),
      options: baseOptions("x"),
    });
  }

  // สลับธีมสว่าง/มืด → วาดใหม่ด้วยสีของธีมนั้น
  let last = null;
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => last && render(last));

  return {
    render(summary) {
      last = summary;
      render(summary);
    },
  };
})();
