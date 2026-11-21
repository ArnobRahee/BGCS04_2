const form = document.getElementById("trip-form");
const statusEl = document.getElementById("status");
const recEl = document.getElementById("recommendation");
const resultsEl = document.getElementById("results");

const money = (n) => n.toLocaleString("en-US", { style: "currency", currency: "USD" });

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  statusEl.textContent = "Planning...";
  recEl.hidden = true;
  resultsEl.replaceChildren();

  const payload = Object.fromEntries(new FormData(form).entries());
  try {
    const res = await fetch("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || data.error) throw new Error(data.error || "Request failed");
    render(data);
    statusEl.textContent = `${data.packages.length} packages for ${data.request.total_travelers} travelers.`;
  } catch (err) {
    statusEl.textContent = err.message;
  }
});

function render(data) {
  const rec = data.recommendation;
  if (rec) {
    recEl.replaceChildren(
      el("h2", null, "Recommended: Package " + rec.package_id),
      el("p", null, rec.reason),
      el("small", null, rec.note ? rec.note : "Source: " + rec.source)
    );
    recEl.hidden = false;
  }
  data.packages.forEach((p) => {
    const card = el("article", "card" + (rec && rec.package_id === p.package_id ? " best" : ""));
    card.append(
      el("h3", null, `Package ${p.package_id} - ${money(p.total_cost)}`),
      el("p", null, `Hotel: ${p.hotel_name} (${p.num_rooms} rooms) - ${money(p.hotel_cost)}`),
      el("p", null, `Meals: ${p.restaurant_name} - ${money(p.meal_cost)}`),
      el("p", null, `Bus: ${p.bus_company} (${p.num_buses}) - ${money(p.bus_cost)}`)
    );
    resultsEl.append(card);
  });
}
