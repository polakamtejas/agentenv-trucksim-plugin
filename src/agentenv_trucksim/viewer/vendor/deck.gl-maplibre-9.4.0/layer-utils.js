// deck.gl
// SPDX-License-Identifier: MIT
// Copyright (c) vis.gl contributors
export const MAPLIBRE_LAST_LAYER_GROUP_ID = 'deck-maplibre-layer-group-last';
export function getMapLibreLayerGroupId(layer) {
    return layer.props.beforeId
        ? `deck-maplibre-layer-group-before:${layer.props.beforeId}`
        : MAPLIBRE_LAST_LAYER_GROUP_ID;
}
//# sourceMappingURL=layer-utils.js.map