/* One cell from Google Photorealistic 3D Tiles. render.mjs calls window.renderCell with the
   camera from rutor (rutor/renderer/google.py) and the contributor's Cesium ion token. */
(function () {
  "use strict";

  const TILE_WAIT_MS = 240000;

  function cartesian(v) {
    return new Cesium.Cartesian3(v[0], v[1], v[2]);
  }

  /* The live map's camera: position and axes in the earth-centred frame, from
     rutor/renderer/google.py. Only the position changes from cell to cell. */
  function applyCamera(viewer, view) {
    viewer.camera.lookAtTransform(Cesium.Matrix4.IDENTITY);
    viewer.camera.position = cartesian(view.position);
    viewer.camera.direction = cartesian(view.direction);
    viewer.camera.up = cartesian(view.up);
    viewer.camera.right = cartesian(view.right);
    const frustum = new Cesium.OrthographicOffCenterFrustum();
    frustum.left = view.frustum.left;
    frustum.right = view.frustum.right;
    frustum.bottom = view.frustum.bottom;
    frustum.top = view.frustum.top;
    frustum.near = view.frustum.near;
    frustum.far = view.frustum.far;
    viewer.camera.frustum = frustum;
  }

  /* Cesium's tile selection assumes a perspective camera. With an orthographic one every tile
     is the same distance in screen terms, so the error is the geometric error over the pixel
     size. Without this the tiles stay coarse. */
  function useOrthographicScreenSpaceError(tileset) {
    const root = tileset && tileset.root;
    if (!root) return;
    const proto = Object.getPrototypeOf(root);
    if (!proto || proto.__pixelbyggetOrtho || typeof proto.getScreenSpaceError !== "function") return;
    const original = proto.getScreenSpaceError;
    proto.getScreenSpaceError = function (frameState, useParentGeometricError, heightFraction) {
      const frustum = frameState.camera && frameState.camera.frustum;
      if (!(frustum instanceof Cesium.OrthographicOffCenterFrustum)) {
        return original.call(this, frameState, useParentGeometricError, heightFraction);
      }
      const parentError = this.parent ? this.parent.geometricError : this._tileset && this._tileset._scaledGeometricError;
      const geometricError = useParentGeometricError ? parentError : this.geometricError;
      if (!geometricError) return 0;
      const context = frameState.context;
      const width = context.drawingBufferWidth;
      const height = context.drawingBufferHeight * (heightFraction || 1);
      const pixelSize = Math.max(frustum.top - frustum.bottom, frustum.right - frustum.left) / Math.max(width, height);
      return geometricError / pixelSize / (frameState.pixelRatio || 1);
    };
    proto.__pixelbyggetOrtho = true;
  }

  function sleep(ms) {
    return new Promise(function (resolve) {
      setTimeout(resolve, ms);
    });
  }

  async function waitForTiles(viewer, tileset) {
    const started = performance.now();
    let settledFrames = 0;
    while (performance.now() - started < TILE_WAIT_MS) {
      viewer.scene.render();
      const stats = tileset.statistics || {};
      const ready = stats.numberOfTilesWithContentReady || 0;
      const pending = stats.numberOfPendingRequests || 0;
      const processing = stats.numberOfTilesProcessing || 0;
      if (ready > 0 && tileset.tilesLoaded === true && pending === 0 && processing === 0) {
        settledFrames += 1;
        // A few quiet frames in a row: a refinement can start right after a load ends.
        if (settledFrames >= 8) return ready;
      } else {
        settledFrames = 0;
      }
      await sleep(250);
    }
    throw new Error("the tiles did not finish loading within " + TILE_WAIT_MS / 1000 + " s");
  }

  /* How high the picture's surface lies, on a grid of HEIGHT_GRID x HEIGHT_GRID points, read
     from the depth of the frame that was just drawn. rutor uses it to tell a place where
     Google has 3D buildings from one where the houses are a photo on flat ground. Null where
     nothing can be read: the caller then leaves the question open. */
  const HEIGHT_GRID = 24;

  function surfaceHeights(viewer) {
    const scene = viewer.scene;
    const canvas = viewer.canvas;
    if (!scene.pickPositionSupported) return null;
    const heights = [];
    for (let j = 0; j < HEIGHT_GRID; j += 1) {
      for (let i = 0; i < HEIGHT_GRID; i += 1) {
        const at = new Cesium.Cartesian2(
          ((i + 0.5) * canvas.width) / HEIGHT_GRID,
          ((j + 0.5) * canvas.height) / HEIGHT_GRID,
        );
        let hit;
        try {
          hit = scene.pickPosition(at);
        } catch (error) {
          return null;
        }
        if (!hit) return null;
        heights.push(Math.round(Cesium.Cartographic.fromCartesian(hit).height * 10) / 10);
      }
    }
    return heights;
  }

  window.renderCell = async function renderCell(config) {
    const probe = document.createElement("canvas");
    if (!probe.getContext("webgl2")) {
      throw new Error("headless Chrome has no WebGL2");
    }
    const view = config.view;
    const viewer = new Cesium.Viewer("cesiumContainer", {
      animation: false,
      timeline: false,
      baseLayerPicker: false,
      geocoder: false,
      homeButton: false,
      sceneModePicker: false,
      navigationHelpButton: false,
      infoBox: false,
      selectionIndicator: false,
      fullscreenButton: false,
      baseLayer: false,
      skyBox: false,
      skyAtmosphere: false,
      terrainProvider: new Cesium.EllipsoidTerrainProvider(),
      requestRenderMode: false,
      msaaSamples: 1,
      creditContainer: document.createElement("div"),
      contextOptions: { webgl: { alpha: false, antialias: false, preserveDrawingBuffer: true } },
    });
    viewer.useBrowserRecommendedResolution = false;
    viewer.resolutionScale = 1;
    const scene = viewer.scene;
    scene.backgroundColor = Cesium.Color.BLACK;
    scene.fog.enabled = false;
    scene.globe.show = false;
    if (scene.sun) scene.sun.show = false;
    if (scene.moon) scene.moon.show = false;
    scene.postProcessStages.fxaa.enabled = false;
    if ("highDynamicRange" in scene) scene.highDynamicRange = false;
    scene.screenSpaceCameraController.enableInputs = false;
    viewer.resize();

    Cesium.Ion.defaultAccessToken = config.token;
    const tileset = await Cesium.Cesium3DTileset.fromIonAssetId(view.ion_asset, { maximumScreenSpaceError: 1 });
    tileset.dynamicScreenSpaceError = false;
    if ("foveatedScreenSpaceError" in tileset) tileset.foveatedScreenSpaceError = false;
    useOrthographicScreenSpaceError(tileset);
    scene.primitives.add(tileset);
    scene.light = new Cesium.DirectionalLight({
      direction: cartesian(view.light.direction),
      intensity: view.light.intensity,
    });

    applyCamera(viewer, view);
    const tiles = await waitForTiles(viewer, tileset);
    applyCamera(viewer, view);
    for (let frame = 0; frame < 3; frame += 1) scene.render();

    return {
      png: viewer.canvas.toDataURL("image/png"),
      width: viewer.canvas.width,
      height: viewer.canvas.height,
      tiles: tiles,
      heights: surfaceHeights(viewer),
      cesium: Cesium.VERSION,
      heading: Cesium.Math.toDegrees(viewer.camera.heading),
      pitch: Cesium.Math.toDegrees(viewer.camera.pitch),
      orthographic: viewer.camera.frustum instanceof Cesium.OrthographicOffCenterFrustum,
    };
  };
})();
