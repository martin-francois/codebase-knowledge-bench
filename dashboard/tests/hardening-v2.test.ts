import {describe, expect, it} from "vitest";
import {TOKEN_VIEWS} from "../src/analysis";

describe("token-accounting-v2 labels", () => {
  it("labels output as inclusive and reasoning as a subset", () => {
    expect(TOKEN_VIEWS.output.label).toContain("including reasoning");
    expect(TOKEN_VIEWS.reasoning.label).toContain("subset of output");
  });
  it("labels the published weighted field as historical v1", () => {
    expect(TOKEN_VIEWS.weighted_load.label).toContain("Historical");
    expect(TOKEN_VIEWS.weighted_load.label).toContain("double-counted");
  });
});
