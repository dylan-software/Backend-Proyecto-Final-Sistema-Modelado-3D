import { S3Client, PutObjectCommand, GetObjectCommand } from "@aws-sdk/client-s3";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";

const ALLOWED_EXTENSIONS = [".glb", ".jpg", ".jpeg", ".png"];
const SIGNED_URL_EXPIRATION_SECONDS = 3600; // 1 hour, configurable

function hasAllowedExtension(key: string): boolean {
  return ALLOWED_EXTENSIONS.some((ext) => key.toLowerCase().endsWith(ext));
}

/** Upload/retrieval of meshes and textures in S3 or R2 (S3-compatible API). */
export class StorageClient {
  constructor(private s3: S3Client, private bucket: string) {}

  async upload(buffer: Uint8Array, key: string, contentType: string): Promise<void> {
    if (!hasAllowedExtension(key)) {
      throw new Error(`StorageClient.upload: extension not allowed for "${key}" (only ${ALLOWED_EXTENSIONS.join(", ")})`);
    }
    await this.s3.send(new PutObjectCommand({ Bucket: this.bucket, Key: key, Body: buffer, ContentType: contentType }));
  }

  async getSignedUrl(key: string, expiresInSeconds = SIGNED_URL_EXPIRATION_SECONDS): Promise<string> {
    const command = new GetObjectCommand({ Bucket: this.bucket, Key: key });
    return getSignedUrl(this.s3, command, { expiresIn: expiresInSeconds });
  }
}

/** Naming convention for stored files. */
export const storagePaths = {
  projectScan: (projectId: string) => `projects/${projectId}/scan.glb`,
  furnitureModel: (furnitureId: string) => `furniture/${furnitureId}/model.glb`,
  furniturePhoto: (furnitureId: string, index: number) => `furniture/${furnitureId}/photo-${index}.jpg`,
};
