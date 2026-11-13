import type { PresignedUpload } from "../api/types";

export const MAX_ATTACHMENT_BYTES = 50 * 1024 * 1024;
export const MAX_AVATAR_BYTES = 5 * 1024 * 1024;

// 拡張子 → Content-Type。ブラウザが付ける file.type は OS によって空だったり揺れたりする
// （zip が application/x-zip-compressed になる、など）ので、拡張子から決めた値を使う
const EXT_TO_TYPE: Record<string, string> = {
  png: "image/png",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  gif: "image/gif",
  webp: "image/webp",
  pdf: "application/pdf",
  doc: "application/msword",
  docx: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  xls: "application/vnd.ms-excel",
  xlsx: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  ppt: "application/vnd.ms-powerpoint",
  pptx: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
  zip: "application/zip",
};

export const ACCEPT_ATTRIBUTE = Object.keys(EXT_TO_TYPE)
  .map((ext) => `.${ext}`)
  .join(",");

export function contentTypeFor(file: File): string | null {
  const ext = file.name.split(".").pop()?.toLowerCase() ?? "";
  return EXT_TO_TYPE[ext] ?? null;
}

export function isImageType(contentType: string) {
  return contentType.startsWith("image/");
}

/**
 * 署名付き POST で S3 / MinIO に直接アップロードする（Django を経由しない）。
 * fetch にはアップロードの進捗を取る API が無いので、XMLHttpRequest を使う。
 */
export function uploadToStorage(upload: PresignedUpload, file: File, onProgress?: (ratio: number) => void) {
  return new Promise<void>((resolve, reject) => {
    const form = new FormData();
    for (const [k, v] of Object.entries(upload.fields)) form.append(k, v);
    form.append("file", file); // S3 の仕様: file は最後のフィールドでなければならない

    const xhr = new XMLHttpRequest();
    xhr.open("POST", upload.url);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total);
    };
    xhr.onload = () =>
      xhr.status >= 200 && xhr.status < 300
        ? resolve()
        : reject(new Error(xhr.status === 400 ? "ストレージに拒否されました（サイズや形式を確認してください）" : `アップロードに失敗しました（${xhr.status}）`));
    xhr.onerror = () => reject(new Error("ネットワークエラーでアップロードできませんでした"));
    xhr.send(form);
  });
}
