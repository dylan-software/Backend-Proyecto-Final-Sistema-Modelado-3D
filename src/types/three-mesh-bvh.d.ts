declare module "three-mesh-bvh" {
  export function computeBoundsTree(...args: unknown[]): void;
  export function disposeBoundsTree(...args: unknown[]): void;
  export function acceleratedRaycast(...args: unknown[]): void;
}
