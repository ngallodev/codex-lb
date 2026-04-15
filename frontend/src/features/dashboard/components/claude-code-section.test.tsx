import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ClaudeCodeSection } from "@/features/dashboard/components/claude-code-section";
import { createDashboardOverview } from "@/test/mocks/factories";

describe("ClaudeCodeSection", () => {
  it("renders Claude Code stats when overview data is present", () => {
    const overview = createDashboardOverview();
    if (!overview.claudeCode) {
      throw new Error("expected claudeCode fixture data");
    }

    render(<ClaudeCodeSection claudeCode={overview.claudeCode} />);

    expect(screen.getByText("Claude Code")).toBeInTheDocument();
    expect(screen.getByText("Claude cost")).toBeInTheDocument();
    expect(screen.getByText("Claude sessions")).toBeInTheDocument();
  });
});
