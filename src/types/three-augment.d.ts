import type {} from "three";

declare module "three" {
  interface BufferGeometry {
    computeBoundsTree?: (...args: unknown[]) => void;
    disposeBoundsTree?: () => void;
  }
}
