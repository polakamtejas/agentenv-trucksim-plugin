// deck.gl
// SPDX-License-Identifier: MIT
// Copyright (c) vis.gl contributors
import { assert } from '@deck.gl/core';
import { getMapLibreRenderParameters } from "./compatibility.js";
import { drawMapLibreLayerGroup, getMapLibreDeckInstance } from "./deck-utils.js";
export default class MapLibreLayerGroup {
    constructor(props) {
        this.type = 'custom';
        this.map = null;
        assert(props.id, 'id is required');
        this.id = props.id;
        this.renderingMode = props.renderingMode || '3d';
        this.beforeId = props.beforeId;
    }
    onAdd(map) {
        this.map = map;
    }
    onRemove() {
        this.map = null;
    }
    render(_gl, parametersOrMatrix, legacyParameters) {
        if (!this.map) {
            return;
        }
        const deck = getMapLibreDeckInstance(this.map);
        if (!deck) {
            return;
        }
        drawMapLibreLayerGroup(deck, this.map, this, getMapLibreRenderParameters(parametersOrMatrix, legacyParameters));
    }
}
//# sourceMappingURL=layer-group.js.map