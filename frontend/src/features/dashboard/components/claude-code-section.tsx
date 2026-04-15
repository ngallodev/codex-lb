import type { ClaudeCodeOverview } from "@/features/dashboard/schemas";
import { StatsGrid } from "@/features/dashboard/components/stats-grid";
import { buildClaudeCodeStats } from "@/features/dashboard/utils";

export type ClaudeCodeSectionProps = {
  claudeCode: ClaudeCodeOverview;
};

export function ClaudeCodeSection({ claudeCode }: ClaudeCodeSectionProps) {
  return (
    <section className="space-y-4">
      <div className="flex items-center gap-3">
        <h2 className="text-[13px] font-medium uppercase tracking-wider text-muted-foreground">Claude Code</h2>
        <div className="h-px flex-1 bg-border" />
      </div>
      <StatsGrid stats={buildClaudeCodeStats(claudeCode)} />
    </section>
  );
}
