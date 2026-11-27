// Item pictures shared by the shop and customer pages.
// The owner's own photo when one was uploaded; otherwise a coloured emoji tile (no copied brand images).
const BKO_ITEM_EMOJI = {
  onion_1kg: "🧅", potato_1kg: "🥔", tomato_1kg: "🍅", haldi_100g: "🟡", jeera_100g: "🌱",
  garam_masala_100g: "🫙", maggi_4pack: "🍜", rice_basmati_1kg: "🍚", rice_sona_1kg: "🍚",
};
const BKO_CATEGORY_EMOJI = {
  atta: "🌾", rice: "🍚", sugar: "🍬", salt: "🧂", oil: "🫗", ghee: "🧈", dal: "🫘", milk: "🥛", curd: "🥣",
  paneer: "🧀", butter: "🧈", bread: "🍞", eggs: "🥚", biscuit: "🍪", noodles: "🍜", tea: "🍵", coffee: "☕",
  spice: "🌶️", veg: "🥬", soap: "🧼", detergent: "🧺", dishwash: "🍽️", toothpaste: "🪥", shampoo: "🧴",
  matchbox: "🔥", pooja: "🪔",
};
const BKO_TILE_COLOURS = ["#ffe0a8", "#c8f0d6", "#cfdcff", "#ffc9c2", "#f7e6bd", "#e8d5ff"];

function bkoEsc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function bkoTileColour(id) {
  let h = 0;
  for (const ch of String(id)) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return BKO_TILE_COLOURS[h % BKO_TILE_COLOURS.length];
}

// size: "sm" (40 px), "md" (72 px) or "lg" (fills its card)
function bkoItemPicture(item, size = "md") {
  if (!item) return `<span class="pic pic-${size} pic-emoji" aria-hidden="true">🛍️</span>`;
  if (item.photo) {
    return `<img class="pic pic-${size}" src="/api/photos/${bkoEsc(item.photo)}" alt="${bkoEsc(item.name)}" loading="lazy">`;
  }
  const emoji = BKO_ITEM_EMOJI[item.id] || BKO_CATEGORY_EMOJI[item.category] || "🛍️";
  return `<span class="pic pic-${size} pic-emoji" style="background:${bkoTileColour(item.id)}" role="img" aria-label="${bkoEsc(item.name)}">${emoji}</span>`;
}
