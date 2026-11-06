// deck.gl
// SPDX-License-Identifier: MIT
// Copyright (c) vis.gl contributors
import { Deck, MapView, _GlobeView as GlobeView, _flatten as flatten } from '@deck.gl/core';
import { getMapLibreElevation, getMapLibreProjection } from "./compatibility.js";
import { getMapLibreLayerGroupId } from "./layer-utils.js";
export const MAPLIBRE_VIEW_ID = 'maplibre';
const MAPLIBRE_DECK_STATES = new WeakMap();
export function createMapLibreInterleavedDeck(map, props) {
    if (MAPLIBRE_DECK_STATES.has(map)) {
        throw new Error('MapLibreOverlay supports one interleaved overlay per map');
    }
    const gl = map.getCanvas().getContext('webgl2');
    if (!gl) {
        throw new Error('MapLibreOverlay interleaved rendering requires WebGL2.');
    }
    const { device: _, ...deckProps } = props;
    const deck = new Deck({ ...deckProps, gl });
    try {
        return createMapLibreDeckInstance(map, deck);
    }
    catch (error) {
        if (getMapLibreDeckInstance(map) === deck) {
            removeMapLibreDeckInstance(map);
        }
        else {
            deck.finalize();
        }
        throw error;
    }
}
export function getMapLibreDefaultParameters(_map, interleaved) {
    return interleaved
        ? {
            depthWriteEnabled: true,
            depthCompare: 'less-equal',
            depthBias: 0,
            blend: true,
            blendColorSrcFactor: 'src-alpha',
            blendColorDstFactor: 'one-minus-src-alpha',
            blendAlphaSrcFactor: 'one',
            blendAlphaDstFactor: 'one-minus-src-alpha',
            blendColorOperation: 'add',
            blendAlphaOperation: 'add'
        }
        : {};
}
export function getMapLibreDefaultView(map) {
    return getMapLibreProjection(map) === 'globe'
        ? new GlobeView({ id: MAPLIBRE_VIEW_ID })
        : new MapView({ id: MAPLIBRE_VIEW_ID });
}
export function getMapLibreViewState(map) {
    const { lng, lat } = map.getCenter();
    const padding = map.getPadding();
    const viewState = {
        longitude: ((lng + 540) % 360) - 180,
        latitude: lat,
        zoom: map.getZoom(),
        bearing: map.getBearing(),
        pitch: map.getPitch(),
        padding: {
            top: padding.top ?? 0,
            bottom: padding.bottom ?? 0,
            left: padding.left ?? 0,
            right: padding.right ?? 0
        },
        repeat: map.getRenderWorldCopies()
    };
    const elevation = getMapLibreElevation(map);
    if (typeof elevation === 'number' && Number.isFinite(elevation)) {
        viewState.position = [0, 0, elevation];
    }
    return viewState;
}
export function createMapLibreDeckInstance(map, deck) {
    const existingState = MAPLIBRE_DECK_STATES.get(map);
    if (existingState) {
        throw new Error('MapLibreOverlay supports one interleaved overlay per map');
    }
    const customRender = deck.props._customRender;
    const onLoad = deck.props.onLoad;
    const state = {
        deck,
        watchingMove: false,
        moveListener: () => {
            if (deck.isInitialized) {
                onMapLibreMove(deck, map);
            }
            else {
                stopWatchingMove(map, state);
            }
        },
        renderListener: () => {
            if (deck.isInitialized) {
                afterMapLibreRender(deck, map);
            }
        }
    };
    MAPLIBRE_DECK_STATES.set(map, state);
    const deckProps = {
        ...deck.props,
        _customRender: () => {
            map.triggerRepaint();
            customRender?.('');
        }
    };
    deckProps.views || (deckProps.views = getMapLibreDefaultView(map));
    Object.assign(deckProps, {
        width: null,
        height: null,
        touchAction: 'unset',
        viewState: getMapLibreViewState(map)
    });
    if (deck.isInitialized) {
        startWatchingMove(map, state);
    }
    else {
        deckProps.onLoad = () => {
            onLoad?.();
            if (MAPLIBRE_DECK_STATES.get(map) === state) {
                startWatchingMove(map, state);
            }
        };
    }
    deck.setProps(deckProps);
    map.on('render', state.renderListener);
    return deck;
}
export function getMapLibreDeckInstance(map) {
    return MAPLIBRE_DECK_STATES.get(map)?.deck;
}
export function removeMapLibreDeckInstance(map) {
    const state = MAPLIBRE_DECK_STATES.get(map);
    if (!state) {
        return;
    }
    stopWatchingMove(map, state);
    map.off('render', state.renderListener);
    state.deck.finalize();
    MAPLIBRE_DECK_STATES.delete(map);
}
export function drawMapLibreLayerGroup(deck, map, group, renderParameters) {
    if (!deck.isInitialized) {
        return;
    }
    let { currentViewport } = deck.userData;
    let clearStack = false;
    if (!currentViewport) {
        currentViewport = getMapLibreViewport(deck, map, renderParameters);
        deck.userData.currentViewport = currentViewport;
        clearStack = true;
    }
    if (!currentViewport) {
        return;
    }
    deck._drawLayers('maplibre-repaint', {
        viewports: [currentViewport],
        layerFilter: params => {
            if (deck.props.layerFilter && !deck.props.layerFilter(params)) {
                return false;
            }
            const layer = params.layer;
            return layer.props.beforeId === group.beforeId;
        },
        clearStack,
        clearCanvas: false
    });
}
function startWatchingMove(map, state) {
    if (!state.watchingMove) {
        state.watchingMove = true;
        map.on('move', state.moveListener);
    }
}
function stopWatchingMove(map, state) {
    if (state.watchingMove) {
        state.watchingMove = false;
        map.off('move', state.moveListener);
    }
}
function getMapLibreViewport(deck, map, renderParameters) {
    const viewState = getMapLibreViewState(map);
    const view = (deck.getView(MAPLIBRE_VIEW_ID) || getMapLibreDefaultView(map));
    if (renderParameters) {
        view.props.nearZMultiplier = 0.2;
        const height = map.getCanvas().clientHeight;
        if (height > 0 &&
            Number.isFinite(renderParameters.nearZ) &&
            Number.isFinite(renderParameters.farZ)) {
            viewState.nearZ = renderParameters.nearZ / height;
            viewState.farZ = renderParameters.farZ / height;
        }
    }
    return view.makeViewport({
        width: deck.width,
        height: deck.height,
        viewState
    });
}
function afterMapLibreRender(deck, map) {
    const deckLayers = flatten(deck.props.layers, Boolean);
    const hasNonMapLibreLayers = deckLayers.some(layer => layer && !map.getLayer(getMapLibreLayerGroupId(layer)));
    let viewports = deck.getViewports();
    const mapLibreViewportIndex = viewports.findIndex(viewport => viewport.id === MAPLIBRE_VIEW_ID);
    const hasNonMapLibreViews = viewports.length > 1 || mapLibreViewportIndex < 0;
    if (hasNonMapLibreLayers || hasNonMapLibreViews) {
        if (mapLibreViewportIndex >= 0) {
            viewports = viewports.slice();
            const mapLibreViewport = getMapLibreViewport(deck, map);
            if (mapLibreViewport) {
                viewports[mapLibreViewportIndex] = mapLibreViewport;
            }
            else {
                viewports.splice(mapLibreViewportIndex, 1);
            }
        }
        deck._drawLayers('maplibre-repaint', {
            viewports,
            layerFilter: params => (!deck.props.layerFilter || deck.props.layerFilter(params)) &&
                (params.viewport.id !== MAPLIBRE_VIEW_ID ||
                    !map.getLayer(getMapLibreLayerGroupId(params.layer))),
            clearCanvas: false
        });
    }
    else {
        const device = deck.device;
        const gl = device?.gl;
        deck.props.onBeforeRender?.({ device, gl });
        deck.props.onAfterRender?.({ device, gl });
    }
    deck.userData.currentViewport = null;
}
function onMapLibreMove(deck, map) {
    deck.setProps({ viewState: getMapLibreViewState(map) });
    deck.needsRedraw({ clearRedrawFlags: true });
}
//# sourceMappingURL=deck-utils.js.map