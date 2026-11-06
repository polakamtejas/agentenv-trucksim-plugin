// deck.gl
// SPDX-License-Identifier: MIT
// Copyright (c) vis.gl contributors
import { Deck, assert } from '@deck.gl/core';
import { createMapLibreInterleavedDeck, getMapLibreDefaultParameters, getMapLibreDefaultView, getMapLibreViewState, MAPLIBRE_VIEW_ID, removeMapLibreDeckInstance } from "./deck-utils.js";
import { getMapLibreProjection } from "./compatibility.js";
import { resolveMapLibreLayerGroups } from "./resolve-layer-groups.js";
/** Renders deck.gl layers over a MapLibre map and synchronizes with its camera. */
export default class MapLibreOverlay {
    /** Creates a MapLibre overlay. */
    constructor(props) {
        this._handleStyleChange = () => {
            resolveMapLibreLayerGroups(this._map, this._props.layers, this._props.layers);
            if (!this._map) {
                return;
            }
            if (getMapLibreProjection(this._map)) {
                this._deck?.setProps({ views: this._getViews(this._map) });
            }
        };
        this._updateContainerSize = () => {
            if (this._map && this._container) {
                const { clientWidth, clientHeight } = this._map.getContainer();
                Object.assign(this._container.style, {
                    width: `${clientWidth}px`,
                    height: `${clientHeight}px`
                });
            }
        };
        this._updateViewState = () => {
            const deck = this._deck;
            const map = this._map;
            if (deck && map) {
                deck.setProps({
                    views: this._getViews(map),
                    viewState: getMapLibreViewState(map)
                });
                if (deck.isInitialized) {
                    deck.redraw();
                }
            }
        };
        // eslint-disable-next-line complexity
        this._handleMouseEvent = (event) => {
            const deck = this._deck;
            if (!deck || !deck.isInitialized) {
                return;
            }
            const mockEvent = {
                type: event.type,
                offsetCenter: event.point || { x: 0, y: 0 },
                srcEvent: event
            };
            const lastDown = this._lastMouseDownPoint;
            if (!event.point && lastDown && event.originalEvent instanceof MouseEvent) {
                mockEvent.deltaX = event.originalEvent.clientX - lastDown.clientX;
                mockEvent.deltaY = event.originalEvent.clientY - lastDown.clientY;
                mockEvent.offsetCenter = {
                    x: lastDown.x + mockEvent.deltaX,
                    y: lastDown.y + mockEvent.deltaY
                };
            }
            switch (mockEvent.type) {
                case 'mousedown':
                    deck._onPointerDown(mockEvent);
                    if (!(event.originalEvent instanceof MouseEvent)) {
                        return;
                    }
                    this._lastMouseDownPoint = {
                        ...mockEvent.offsetCenter,
                        clientX: event.originalEvent.clientX,
                        clientY: event.originalEvent.clientY
                    };
                    break;
                case 'dragstart':
                    mockEvent.type = 'panstart';
                    deck._onEvent(mockEvent);
                    break;
                case 'drag':
                    mockEvent.type = 'panmove';
                    deck._onEvent(mockEvent);
                    break;
                case 'dragend':
                    mockEvent.type = 'panend';
                    deck._onEvent(mockEvent);
                    break;
                case 'click':
                    mockEvent.tapCount = 1;
                    deck._onEvent(mockEvent);
                    break;
                case 'dblclick':
                    mockEvent.type = 'click';
                    mockEvent.tapCount = 2;
                    deck._onEvent(mockEvent);
                    break;
                case 'mousemove':
                    mockEvent.type = 'pointermove';
                    deck._onPointerMove(mockEvent);
                    break;
                case 'mouseout':
                    mockEvent.type = 'pointerleave';
                    deck._onPointerMove(mockEvent);
                    break;
                default:
                    return;
            }
        };
        const { interleaved = false } = props;
        this._interleaved = interleaved;
        this._props = this._filterProps(props);
    }
    _filterProps(props) {
        const { interleaved: _, useDevicePixels, device, ...deckProps } = props;
        if (!this._interleaved) {
            if (useDevicePixels !== undefined) {
                Object.assign(deckProps, { useDevicePixels });
            }
            if (device !== undefined) {
                Object.assign(deckProps, { device });
            }
        }
        return deckProps;
    }
    /** Updates partial properties of the underlying Deck instance. */
    setProps(props) {
        if (this._interleaved && props.layers) {
            resolveMapLibreLayerGroups(this._map, this._props.layers, props.layers);
        }
        Object.assign(this._props, this._filterProps(props));
        if (this._deck && this._map) {
            this._deck.setProps({
                ...this._props,
                views: this._getViews(this._map),
                parameters: {
                    ...getMapLibreDefaultParameters(this._map, this._interleaved),
                    ...this._props.parameters
                }
            });
        }
    }
    /** Called when the control is added to a map. */
    onAdd(map) {
        this._map = map;
        try {
            return this._interleaved ? this._onAddInterleaved(map) : this._onAddOverlaid(map);
        }
        catch (error) {
            if (this._deck) {
                if (this._interleaved) {
                    this._onRemoveInterleaved(map);
                }
                else {
                    this._onRemoveOverlaid(map);
                }
            }
            this._deck = undefined;
            this._map = undefined;
            this._container = undefined;
            throw error;
        }
    }
    /** Called when the control is removed from a map. */
    onRemove(_map) {
        const map = this._map;
        if (map) {
            if (this._interleaved) {
                this._onRemoveInterleaved(map);
            }
            else {
                this._onRemoveOverlaid(map);
            }
        }
        this._deck = undefined;
        this._map = undefined;
        this._container = undefined;
    }
    /** Default control position. */
    getDefaultPosition() {
        return 'top-left';
    }
    /** Forwards to Deck.pickObject. */
    pickObject(params) {
        assert(this._deck);
        return this._deck.pickObject(params);
    }
    /** Forwards to Deck.pickMultipleObjects. */
    pickMultipleObjects(params) {
        assert(this._deck);
        return this._deck.pickMultipleObjects(params);
    }
    /** Forwards to Deck.pickObjects. */
    pickObjects(params) {
        assert(this._deck);
        return this._deck.pickObjects(params);
    }
    /** Removes the control from its map and releases resources. */
    finalize() {
        this._map?.removeControl(this);
    }
    /** Returns the basemap canvas in interleaved mode and Deck's canvas otherwise. */
    getCanvas() {
        if (!this._map) {
            return null;
        }
        return this._interleaved ? this._map.getCanvas() : this._deck.getCanvas();
    }
    _onAddOverlaid(map) {
        const container = document.createElement('div');
        Object.assign(container.style, {
            position: 'absolute',
            left: 0,
            top: 0,
            textAlign: 'initial',
            pointerEvents: 'none'
        });
        this._container = container;
        this._deck = new Deck({
            ...this._props,
            parent: container,
            deviceProps: {
                ...this._props.deviceProps,
                createCanvasContext: {
                    ...(typeof this._props.deviceProps?.createCanvasContext === 'object'
                        ? this._props.deviceProps.createCanvasContext
                        : undefined),
                    pixelSizeSource: 'css-dpr'
                }
            },
            parameters: {
                ...getMapLibreDefaultParameters(map, false),
                ...this._props.parameters
            },
            views: this._getViews(map),
            viewState: getMapLibreViewState(map)
        });
        map.on('resize', this._updateContainerSize);
        map.on('render', this._updateViewState);
        map.on('mousedown', this._handleMouseEvent);
        map.on('dragstart', this._handleMouseEvent);
        map.on('drag', this._handleMouseEvent);
        map.on('dragend', this._handleMouseEvent);
        map.on('mousemove', this._handleMouseEvent);
        map.on('mouseout', this._handleMouseEvent);
        map.on('click', this._handleMouseEvent);
        map.on('dblclick', this._handleMouseEvent);
        this._updateContainerSize();
        return container;
    }
    _onAddInterleaved(map) {
        this._deck = createMapLibreInterleavedDeck(map, {
            ...this._props,
            views: this._getViews(map),
            parameters: {
                ...getMapLibreDefaultParameters(map, true),
                ...this._props.parameters
            }
        });
        map.on('styledata', this._handleStyleChange);
        resolveMapLibreLayerGroups(map, [], this._props.layers);
        return document.createElement('div');
    }
    _onRemoveOverlaid(map) {
        map.off('resize', this._updateContainerSize);
        map.off('render', this._updateViewState);
        map.off('mousedown', this._handleMouseEvent);
        map.off('dragstart', this._handleMouseEvent);
        map.off('drag', this._handleMouseEvent);
        map.off('dragend', this._handleMouseEvent);
        map.off('mousemove', this._handleMouseEvent);
        map.off('mouseout', this._handleMouseEvent);
        map.off('click', this._handleMouseEvent);
        map.off('dblclick', this._handleMouseEvent);
        this._deck?.finalize();
    }
    _onRemoveInterleaved(map) {
        map.off('styledata', this._handleStyleChange);
        try {
            resolveMapLibreLayerGroups(map, this._props.layers, []);
        }
        finally {
            removeMapLibreDeckInstance(map);
        }
    }
    _getViews(map) {
        if (!this._props.views) {
            return getMapLibreDefaultView(map);
        }
        const views = Array.isArray(this._props.views) ? this._props.views : [this._props.views];
        const hasMapLibreView = views.some((view) => view.id === MAPLIBRE_VIEW_ID);
        if (hasMapLibreView) {
            return this._props.views;
        }
        return [getMapLibreDefaultView(map), ...views];
    }
}
//# sourceMappingURL=overlay.js.map