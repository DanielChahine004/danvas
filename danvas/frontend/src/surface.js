// The canvas-engine seam.
//
// danvas is built on tldraw, but most of bridge.js's imperative calls to the
// engine — move the camera, create/update/delete a shape, reorder z, export to
// an image — are generic "infinite canvas" operations any engine exposes. This
// module fences those behind one interface so the engine dependency is in *one*
// file instead of scattered across bridge.js. The day danvas evaluates another
// engine (react-flow, a custom canvas), this is the file you reimplement.
//
// Every method is a variadic pass-through to the tldraw editor, so routing a
// call through the surface is behaviourally identical to calling the editor
// directly — the seam adds a name, not logic.
//
// What is DELIBERATELY NOT here (the parts tldraw is load-bearing for, which no
// thin interface can make engine-neutral — see the README licence/architecture
// notes): the tldraw `store` as danvas's drawing-sync + presence substrate
// (`store.listen` / `applyDiff` / `mergeRemoteChanges`), arrow `createBindings`,
// `updateInstanceState`, `sideEffects`, and the 7 `ShapeUtil` classes in
// canvas.jsx that *are* the panel model. bridge.js still touches those through
// `editor` directly, on purpose — so the real, irreducible coupling stays
// visible rather than hidden behind a leaky abstraction.
export function createTldrawSurface(editor) {
  return {
    // ✅ Camera & coordinates — pure geometry, every engine has equivalents.
    camera: {
      get:                  (...a) => editor.getCamera(...a),
      set:                  (...a) => editor.setCamera(...a),
      setOptions:           (...a) => editor.setCameraOptions(...a),
      zoomLevel:            (...a) => editor.getZoomLevel(...a),
      viewportScreenBounds: (...a) => editor.getViewportScreenBounds(...a),
      viewportPageBounds:   (...a) => editor.getViewportPageBounds(...a),
      currentPageBounds:    (...a) => editor.getCurrentPageBounds(...a),
    },

    // 🟡 Shape CRUD — mechanical, but assumes a shape store keyed by id.
    shapes: {
      get:     (...a) => editor.getShape(...a),
      create:  (...a) => editor.createShape(...a),
      update:  (...a) => editor.updateShape(...a),
      delete:  (...a) => editor.deleteShape(...a),
      pageIds: (...a) => editor.getCurrentPageShapeIds(...a),
    },

    // ✅ Z-order.
    zorder: {
      toFront:  (...a) => editor.bringToFront(...a),
      toBack:   (...a) => editor.sendToBack(...a),
      forward:  (...a) => editor.bringForward(...a),
      backward: (...a) => editor.sendBackward(...a),
    },

    // ✅ Export — this is what canvas.screenshot() / get_image rides on, now
    // engine-agnostic: a new backend swaps these three and screenshots keep working.
    export: {
      toImage:    (...a) => editor.toImage(...a),
      getContent: (...a) => editor.getContentFromCurrentPage(...a),
      putContent: (...a) => editor.putContentOntoCurrentPage(...a),
    },

    // The engine's root DOM element (for ResizeObserver / screen-coord math).
    container: (...a) => editor.getContainer(...a),
  }
}
