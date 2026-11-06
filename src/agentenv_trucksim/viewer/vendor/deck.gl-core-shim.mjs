// @deck.gl/maplibre (vendor/deck.gl-maplibre-9.4.0, ES modules only) imports these from @deck.gl/core; cinema.html's
// import map points that name here, at the same objects the deck.gl bundle (vendor/deck.gl-9.4.0.min.js) exposes.
const d = globalThis.deck;
if (!d) throw new Error("load vendor/deck.gl-9.4.0.min.js before @deck.gl/maplibre");
export const { Deck, MapView, _GlobeView, _flatten, assert } = d;
