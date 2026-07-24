import {describe, expect, it} from "vitest";
import {TOKEN_VIEWS} from "../src/analysis";

describe("token-accounting-current labels", () => {
  it("labels output as inclusive and reasoning as a subset", () => {
    expect(TOKEN_VIEWS.output.label).toContain("including reasoning");
    expect(TOKEN_VIEWS.reasoning.label).toContain("subset of output");
  });
  it("does not expose a cache-weighted token view", () => {
    expect(TOKEN_VIEWS).not.toHaveProperty("weighted_load");
  });
  it("exposes total reported tokens as the primary token view", () => {
    expect(TOKEN_VIEWS.total_reported).toEqual({
      label: "Total reported tokens",
      metric: "total_reported_tokens",
    });
  });
});
