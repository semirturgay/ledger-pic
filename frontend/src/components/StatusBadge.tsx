import { CircleAlert, CircleCheck, Clock, ImageOff, LoaderCircle } from "lucide-react";

import type { ReceiptListItem } from "@/lib/api";
import { failureCopy } from "@/lib/failures";

type Meta = { label: string; Icon: typeof CircleCheck; spin?: boolean };
type BadgeFields = Pick<ReceiptListItem, "status" | "failure_reason">;

function meta(receipt: BadgeFields): Meta {
  switch (receipt.status) {
    case "processing":
      // Queued, reading or being retried behind the scenes: to the user it's all just "Processing".
      return { label: "Processing", Icon: LoaderCircle, spin: true };
    case "needs_review":
      return { label: "Needs review", Icon: Clock };
    case "confirmed":
      return { label: "Confirmed", Icon: CircleCheck };
    case "failed":
      return {
        label: failureCopy(receipt.failure_reason).badge,
        Icon: receipt.failure_reason === "not_a_receipt" ? ImageOff : CircleAlert,
      };
  }
}

export function StatusBadge({ receipt }: { receipt: BadgeFields }) {
  const { label, Icon, spin } = meta(receipt);
  return (
    <span className={`pill ${receipt.status}`}>
      <Icon size={13} className={spin ? "spin" : undefined} />
      {label}
    </span>
  );
}
