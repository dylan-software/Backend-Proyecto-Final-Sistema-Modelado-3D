import { NodeIO } from "@gltf-transform/core";
import { Room } from "./Room";
import type { Point2D } from "./Room";

/**
 * Loads a scanned .glb file and builds a Room from its embedded scan
 * metadata (polygon vertices + height), validating that it carries
 * real metric scale.
 *
 * Expected convention: the model must carry, in its extras, an
 * `scan` object with the polygon already detected by the
 * segmentation/RANSAC step — this parser only validates and translates
 * that data into the domain classes; it does not re-segment.
 *
 * Note: the primary scanning flow is now AR polygon tracing directly
 * in the browser (tap-to-place vertices), so this GLB import path is
 * an optional alternative for pre-scanned files.
 */
export async function parseRoomFromGLB(buffer: Uint8Array): Promise<Room> {
  const io = new NodeIO();

  let document;
  try {
    document = await io.readBinary(buffer);
  } catch (err) {
    throw new Error(`parseRoomFromGLB: the file is not a valid GLB (${(err as Error).message})`);
  }

  const root = document.getRoot();
  const extras = root.getExtras() as Record<string, unknown> | undefined;
  const scan = extras?.scan as { vertices?: Point2D[]; heightM?: number } | undefined;

  if (!scan || !scan.vertices || scan.vertices.length < 3 || !scan.heightM) {
    throw new Error(
      "parseRoomFromGLB: the file does not carry metric scale nor the room polygon " +
        "(extras.scan.{vertices[], heightM}). Make sure the AR scan wrote that metadata before exporting the GLB."
    );
  }

  return new Room(scan.vertices, scan.heightM);
}
