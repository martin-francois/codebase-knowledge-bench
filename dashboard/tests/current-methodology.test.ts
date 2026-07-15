import {describe, expect, it} from "vitest";
import {TOKEN_VIEWS} from "../src/analysis";

describe("token-accounting-current labels", () => {
  it("labels output as inclusive and reasoning as a subset", () => {
    expect(TOKEN_VIEWS.output.label).toContain("including reasoning");
    expect(TOKEN_VIEWS.reasoning.label).toContain("subset of output");
  });
  it("labels the sole current weighted-load field without a historical alias", () => {
    expect(TOKEN_VIEWS.weighted_load.label).toBe("Modeled weighted token load");
    expect(TOKEN_VIEWS.weighted_load.label).not.toContain("double-counted");
  });
});
