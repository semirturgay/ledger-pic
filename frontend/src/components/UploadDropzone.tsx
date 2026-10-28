"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Camera, ImagePlus, Images, LoaderCircle } from "lucide-react";
import { useState, type ChangeEvent, type DragEvent } from "react";

import { useToast } from "@/components/Toaster";
import { duplicateOf, type Receipt } from "@/lib/api";
import { queryKeys } from "@/lib/queryKeys";
import { getToken } from "@/lib/auth";
import { addToList, refreshReceipts, uploadWithRetry } from "@/lib/receipts";

// Mirrors the server limits for a fast error; the server enforces them regardless.
const MAX_BYTES = 10 * 1024 * 1024;
const ACCEPTED = ["image/jpeg", "image/png"];
const MAX_FILES = 20;
const PARALLEL_UPLOADS = 3;

function rejectionReason(file: File): string | null {
  if (!ACCEPTED.includes(file.type)) return "not a JPEG or PNG image";
  if (file.size > MAX_BYTES) return "larger than 10 MB";
  return null;
}

/** Ask once, on a user action, so finished receipts can notify while the tab is in the background. */
function askForNotificationPermission() {
  if (typeof Notification !== "undefined" && Notification.permission === "default") {
    void Notification.requestPermission();
  }
}

export function UploadDropzone() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(0);

  async function uploadAll(fileList: FileList | File[]) {
    const files = Array.from(fileList).slice(0, MAX_FILES);
    // The whole batch belongs to whoever is signed in now, even if another tab signs in as someone else.
    const signedIn = getToken();
    if (files.length === 0 || !signedIn) return;
    const token: string = signedIn;
    askForNotificationPermission();

    const rejected = files.flatMap((f) => {
      const reason = rejectionReason(f);
      return reason ? [`${f.name}: ${reason}`] : [];
    });
    const valid = files.filter((f) => rejectionReason(f) === null);
    const failed: string[] = [];
    const duplicates: string[] = []; // ids of the receipts these files were already uploaded as
    let uploaded = 0;
    let queued = 0;

    setUploading((n) => n + valid.length);
    const queue = [...valid];
    async function uploadNext(): Promise<void> {
      const file = queue.shift();
      if (!file) return;
      try {
        const receipt: Receipt | "queued" = await uploadWithRetry(file, token);
        if (receipt === "queued") {
          queued++;
        } else {
          queryClient.setQueryData(queryKeys.receipt(receipt.id), receipt);
          addToList(queryClient, receipt);
          uploaded++;
        }
      } catch (err) {
        const existing = duplicateOf(err);
        if (existing) duplicates.push(existing);
        else failed.push(`${file.name}: ${(err as Error).message}`);
      } finally {
        setUploading((n) => n - 1);
      }
      return uploadNext();
    }
    await Promise.all(Array.from({ length: Math.min(PARALLEL_UPLOADS, valid.length) }, uploadNext));

    refreshReceipts(queryClient);

    if (uploaded > 0) {
      toast({
        tone: "info",
        title: uploaded === 1 ? "Receipt uploaded" : `${uploaded} receipts uploaded`,
        body: "The AI is reading them in the background. We'll let you know when they're ready to review.",
      });
    }
    if (queued > 0) {
      toast({
        tone: "info",
        title: queued === 1 ? "Saved on this device" : `${queued} receipts saved on this device`,
        body: "The server can't be reached right now. They'll upload automatically once it can.",
      });
    }
    if (duplicates.length > 0) {
      toast({
        tone: "info",
        title: duplicates.length === 1 ? "Already uploaded" : `${duplicates.length} photos were already uploaded`,
        body: "LedgerPic keeps one copy of each photo, so nothing was added.",
        action: { label: duplicates.length === 1 ? "Open it" : "Open the first", href: `/receipts/${duplicates[0]}` },
      });
    }
    const problems = [...rejected, ...failed];
    if (problems.length > 0) {
      toast({
        tone: "error",
        title: problems.length === 1 ? "One file wasn't uploaded" : `${problems.length} files weren't uploaded`,
        body: problems.join(" · "),
      });
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    void uploadAll(event.dataTransfer.files);
  }

  const busy = uploading > 0;
  const onPicked = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) void uploadAll(e.target.files);
    e.target.value = "";
  };
  return (
    <div
      className={`dropzone${dragging ? " dragging" : ""}${busy ? " busy" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
    >
      {/* Library / file picker: several files at once. */}
      <label className="dropzone-main">
        <input type="file" className="sr-only" multiple accept={ACCEPTED.join(",")} onChange={onPicked} />
        <span className="dropzone-icon">{busy ? <LoaderCircle size={24} className="spin" /> : <ImagePlus size={24} />}</span>
        <span>
          <span className="dropzone-title" style={{ display: "block" }}>
            {busy ? `Uploading ${uploading} receipt${uploading === 1 ? "" : "s"}…` : "Snap it, drop it, done"}
          </span>
          <span className="dropzone-hint pointer-only">
            Drop one or more receipt photos here, or click to browse. JPEG or PNG, up to 10 MB each.
          </span>
          <span className="dropzone-hint touch-only">
            Take a photo, or choose one or more from your library. JPEG or PNG, up to 10 MB each.
          </span>
        </span>
        <span className="btn btn-primary pointer-only" aria-hidden="true">
          Upload receipts
        </span>
      </label>
      {/* Touch devices: Android Chrome only offers the camera for image/* without `multiple`,
          so the camera gets its own input that opens the rear camera directly. */}
      <div className="dropzone-actions touch-only">
        <label className="btn btn-primary">
          <input type="file" className="sr-only" accept="image/*" capture="environment" onChange={onPicked} />
          <Camera size={16} /> Take photo
        </label>
        <label className="btn">
          <input type="file" className="sr-only" multiple accept={ACCEPTED.join(",")} onChange={onPicked} />
          <Images size={16} /> Choose photos
        </label>
      </div>
    </div>
  );
}
