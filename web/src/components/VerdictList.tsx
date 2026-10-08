import { Chip } from "@/components/ui";
import type { Claim, Verdict, VerifyReport } from "@/lib/auditApi";

/*
 * An INDEX into the check report, not a retelling of it.
 *
 * Every string here is copied verbatim out of the JSON — the claim text, the
 * verdict's own meaning, the reason, what was inherited. Nothing is
 * paraphrased, because the one human-readable contract lives server-side
 * (`render_report`) and is printed below this, in full. Same rule as
 * EvidenceCard: a second, prettier rendering in the browser would be a
 * version nobody has reviewed.
 *
 * 🔴 What this component must never do: style NOT_COVERED or NOT_CHECKABLE as
 * errors. "We can't check this" is a CORRECT outcome — coverage is narrow on
 * purpose, and what that buys is that every number we can check was checked
 * field by field by a person who signed for it. Painting it red would sell
 * the deliberate trade as a defect.
 */

type Tone = "neutral" | "success" | "warn" | "danger";

const TONE: Record<Verdict, { mark: string; chip: Tone }> = {
  CONSISTENT: { mark: "✅", chip: "success" },
  // danger, not warn: the paste asserts a number the annual report does not
  // support. That is the finding this page exists to surface.
  INCONSISTENT: { mark: "❌", chip: "danger" },
  AMBIGUOUS_BASIS: { mark: "⚠️", chip: "warn" },
  // Neutral, not warn: see the note above.
  NOT_COVERED: { mark: "—", chip: "neutral" },
  NOT_CHECKABLE: { mark: "·", chip: "neutral" },
};

const LABEL: Record<Verdict, string> = {
  CONSISTENT: "一致",
  INCONSISTENT: "不一致",
  AMBIGUOUS_BASIS: "口径未声明",
  NOT_COVERED: "核不了",
  NOT_CHECKABLE: "不是可核声明",
};

const ORDER: Verdict[] = [
  "CONSISTENT",
  "INCONSISTENT",
  "AMBIGUOUS_BASIS",
  "NOT_COVERED",
  "NOT_CHECKABLE",
];

export function VerdictList({ report }: { report: VerifyReport }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        {/* All five, always — a missing chip and a zero are not the same thing. */}
        {ORDER.map((v) => (
          <Chip key={v} tone={TONE[v].chip}>
            {LABEL[v]} {report.counts[v] ?? 0}
          </Chip>
        ))}
      </div>

      <ol className="flex flex-col gap-2.5">
        {report.claims.map((c, i) => (
          <ClaimRow key={i} index={i + 1} claim={c} />
        ))}
      </ol>
    </div>
  );
}

function ClaimRow({ index, claim }: { index: number; claim: Claim }) {
  const tone = TONE[claim.verdict];
  return (
    <li className="rounded-md border border-border bg-surface-2 px-3 py-2.5">
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span aria-hidden className="text-sm">
          {tone.mark}
        </span>
        <span className="font-mono text-xs text-fg-3 tabular-nums">
          {index}.
        </span>
        <span className="text-[13px] leading-relaxed font-medium text-fg text-pretty">
          {claim.text}
        </span>
        <Chip tone={tone.chip} className="ml-auto">
          {LABEL[claim.verdict]}
        </Chip>
      </div>

      {/*
        🔴 Inherited subject/period, printed. If the system carried over the
        wrong company or the wrong year, this line is where the reader sees it.
        Dropping it to save a row would hide precisely the mistake that is
        hardest to catch from the verdict alone.
      */}
      {claim.inherited ? (
        <p className="mt-1.5 font-mono text-xs text-fg-3">（{claim.inherited}）</p>
      ) : null}

      {claim.reason ? (
        <p className="mt-1.5 text-xs leading-relaxed text-fg-2 text-pretty">
          {claim.reason}
        </p>
      ) : null}
    </li>
  );
}
