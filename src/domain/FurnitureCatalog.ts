import { Furniture } from "./Furniture";
import type { FurnitureCategory } from "./Furniture";

/** In-memory catalog of available furniture/objects. */
export class FurnitureCatalog {
  #items: Furniture[] = [];

  add(item: Furniture): void {
    if (this.findById(item.id)) {
      throw new Error(`FurnitureCatalog: an item with id ${item.id} already exists`);
    }
    this.#items.push(item);
  }

  remove(id: string): boolean {
    const before = this.#items.length;
    this.#items = this.#items.filter((m) => m.id !== id);
    return this.#items.length < before;
  }

  findById(id: string): Furniture | null {
    return this.#items.find((m) => m.id === id) ?? null;
  }

  findByCategory(category: FurnitureCategory): Furniture[] {
    return this.#items.filter((m) => m.getCategory() === category);
  }

  listAll(): ReadonlyArray<Furniture> {
    return this.#items;
  }
}
