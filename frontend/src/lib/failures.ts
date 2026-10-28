import type { FailureReason } from "@/lib/api";

type FailureCopy = {
  /** Short status label in lists. */
  badge: string;
  /** Review page headline. */
  title: string;
  /** What the user can do next, shown under the server's message. */
  nextStep: string;
  /** Label for the retry button. */
  retryLabel: string;
  /** "Finished" notification. */
  toastTitle: string;
  toastBody: string;
};

const COPY: Record<FailureReason, FailureCopy> = {
  not_a_receipt: {
    badge: "Not a receipt",
    title: "This doesn't look like a receipt",
    nextStep: "Delete it if you uploaded the wrong photo. If it is a receipt, try again or fill it in yourself.",
    retryLabel: "It is a receipt, try again",
    toastTitle: "That image isn't a receipt",
    toastBody: "Delete it, or open it if you think the AI got it wrong.",
  },
  nothing_readable: {
    badge: "Unreadable",
    title: "Nothing on this receipt could be read",
    nextStep: "A sharper, well-lit photo usually helps. You can also fill it in yourself.",
    retryLabel: "Try again",
    toastTitle: "A receipt couldn't be read",
    toastBody: "Try a sharper photo, or fill in the details yourself.",
  },
  image_unreadable: {
    badge: "Bad image",
    title: "This image file is damaged",
    nextStep: "It wasn't kept. Delete it and upload the photo again, or fill the details in yourself.",
    retryLabel: "Try again",
    toastTitle: "An image couldn't be opened",
    toastBody: "The file looks damaged. Delete it and upload it again.",
  },
  ai_unavailable: {
    badge: "Failed",
    title: "The AI couldn't process this receipt",
    nextStep: "Try again in a few minutes, or fill the details in yourself.",
    retryLabel: "Retry extraction",
    toastTitle: "A receipt couldn't be processed",
    toastBody: "The AI service is unavailable. Open it to retry, or fill in the details yourself.",
  },
};

export function failureCopy(reason: FailureReason | null): FailureCopy {
  return COPY[reason ?? "ai_unavailable"];
}
