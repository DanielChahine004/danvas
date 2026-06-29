// Structural fast-path for image-frame panels — the built-in VideoFeed and any
// custom React/Custom panel that streams encoded image bytes (JPEG/PNG/WebP) over
// the binary channel (`canvas.onFrame`). One shared, optimised pipeline so every
// such panel benefits, not just one.
//
// Per frame it:
//   1. decodes OFF the main thread via `createImageBitmap` (no <img> load cycle,
//      no per-frame Blob URL alloc/revoke, no main-thread JPEG decode stall),
//   2. blits with the GPU-fast `bitmaprenderer` context (`transferFromImageBitmap`
//      is a zero-copy ownership transfer — no 2D compositing), falling back to a
//      2D `drawImage`, then to an <img> decode on browsers without
//      `createImageBitmap`.
//
// Frames are COALESCED: while a decode is in flight only the latest pending frame
// is retained, so a fast producer (or a momentary main-thread hitch) never builds
// a backlog of stale work — it always catches up to the newest frame.
//
// The canvas is painted at the frame's native resolution; sizing/letterboxing is
// left to CSS on the <canvas> element (e.g. `width:100%;height:100%;object-fit:
// contain`), keeping the hot path a pure blit and letting each panel control
// layout declaratively.

export interface PaintFrameOpts {
  /** Fired once, right after the first frame is painted (e.g. to drop a placeholder). */
  onActive?: () => void
}

/**
 * Pump an image-frame stream onto a <canvas>. `subscribe` registers a binary-frame
 * callback and returns an unsubscribe; the returned disposer stops painting and
 * detaches. Reusable by any panel — pass the panel's own frame subscription.
 */
export function paintFrameStream(
  subscribe: (cb: (buf: ArrayBuffer) => void) => () => void,
  target: HTMLCanvasElement,
  opts: PaintFrameOpts = {},
): () => void {
  let disposed = false
  let inflight = false
  let pending: ArrayBuffer | null = null
  let active = false

  const useBitmap = typeof createImageBitmap === 'function'
  let bitmapCtx: ImageBitmapRenderingContext | null = null
  let ctx2d: CanvasRenderingContext2D | null = null
  if (useBitmap) {
    try {
      bitmapCtx = target.getContext('bitmaprenderer')
    } catch {
      bitmapCtx = null
    }
    if (!bitmapCtx) ctx2d = target.getContext('2d')
  } else {
    ctx2d = target.getContext('2d')
  }

  const markActive = () => {
    if (!active) {
      active = true
      opts.onActive?.()
    }
  }

  const drawSource = (src: ImageBitmap | HTMLImageElement, w: number, h: number) => {
    if (target.width !== w) target.width = w
    if (target.height !== h) target.height = h
    ctx2d!.drawImage(src, 0, 0)
  }

  // After a decode settles, immediately start the latest queued frame (if any) so
  // we always converge on the newest frame without queuing stale ones.
  const drain = () => {
    if (pending && !disposed) {
      const buf = pending
      pending = null
      decode(buf)
    } else {
      inflight = false
    }
  }

  const decode = (buf: ArrayBuffer) => {
    inflight = true
    if (useBitmap) {
      createImageBitmap(new Blob([buf]))
        .then((bmp) => {
          if (disposed) {
            bmp.close()
          } else if (bitmapCtx) {
            bitmapCtx.transferFromImageBitmap(bmp) // transfers ownership; no close
            markActive()
          } else if (ctx2d) {
            drawSource(bmp, bmp.width, bmp.height)
            bmp.close()
            markActive()
          } else {
            bmp.close()
          }
          drain()
        })
        .catch(drain)
      return
    }
    // Last-resort fallback for browsers without createImageBitmap.
    const url = URL.createObjectURL(new Blob([buf]))
    const img = new Image()
    img.onload = () => {
      if (!disposed && ctx2d) {
        drawSource(img, img.naturalWidth, img.naturalHeight)
        markActive()
      }
      URL.revokeObjectURL(url)
      drain()
    }
    img.onerror = () => {
      URL.revokeObjectURL(url)
      drain()
    }
    img.src = url
  }

  const onFrame = (buf: any) => {
    if (disposed || !(buf instanceof ArrayBuffer)) return
    if (inflight) {
      pending = buf // coalesce: keep only the most recent frame
      return
    }
    decode(buf)
  }

  const unsub = subscribe(onFrame)
  return () => {
    disposed = true
    pending = null
    unsub()
  }
}
