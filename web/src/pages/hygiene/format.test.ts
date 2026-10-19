import { expect, it } from "vitest";
import { similarityLabel } from "./format";

it("renders similarity as a percentage and a dash when unknown", () => {
  expect(similarityLabel(0.934)).toBe("93%");
  expect(similarityLabel(1)).toBe("100%");
  expect(similarityLabel(null)).toBe("—");
});
