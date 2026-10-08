export type FurnitureCategory = "sofa" | "table" | "chair" | "bed" | "shelf" | "lamp" | "other";

export interface FurnitureJSON {
  id: string;
  name: string;
  widthCm: number;
  heightCm: number;
  depthCm: number;
  category: FurnitureCategory;
  material: string;
  price: number;
  style: string;
}

/**
 * Represents a catalog item (furniture or any manually created object)
 * with its real dimensions. `name` is required — every object, whether
 * from the official catalog or created manually by the user, must be named.
 */
export class Furniture {
  readonly id: string;
  private name: string;
  private widthCm: number;
  private heightCm: number;
  private depthCm: number;
  private category: FurnitureCategory;
  private material: string;
  private price: number;
  private style: string;

  constructor(params: {
    id: string;
    name: string;
    widthCm: number;
    heightCm: number;
    depthCm: number;
    category: FurnitureCategory;
    material: string;
    price: number;
    style: string;
  }) {
    if (!params.name || params.name.trim().length === 0) {
      throw new Error("Furniture: name is required");
    }
    this.validateDimension(params.widthCm, "widthCm");
    this.validateDimension(params.heightCm, "heightCm");
    this.validateDimension(params.depthCm, "depthCm");

    this.id = params.id;
    this.name = params.name;
    this.widthCm = params.widthCm;
    this.heightCm = params.heightCm;
    this.depthCm = params.depthCm;
    this.category = params.category;
    this.material = params.material;
    this.price = params.price;
    this.style = params.style;
  }

  private validateDimension(value: number, field: string): void {
    if (value <= 0) {
      throw new Error(`Furniture: ${field} must be greater than 0 (received: ${value})`);
    }
  }

  updateDimensions(widthCm: number, heightCm: number, depthCm: number): void {
    this.validateDimension(widthCm, "widthCm");
    this.validateDimension(heightCm, "heightCm");
    this.validateDimension(depthCm, "depthCm");
    this.widthCm = widthCm;
    this.heightCm = heightCm;
    this.depthCm = depthCm;
  }

  getDimensionsCm(): { width: number; height: number; depth: number } {
    return { width: this.widthCm, height: this.heightCm, depth: this.depthCm };
  }

  getCategory(): FurnitureCategory {
    return this.category;
  }

  getStyle(): string {
    return this.style;
  }

  getName(): string {
    return this.name;
  }

  /** Must match the `furniture` SQL table 1:1. */
  toJSON(): FurnitureJSON {
    return {
      id: this.id,
      name: this.name,
      widthCm: this.widthCm,
      heightCm: this.heightCm,
      depthCm: this.depthCm,
      category: this.category,
      material: this.material,
      price: this.price,
      style: this.style,
    };
  }
}
