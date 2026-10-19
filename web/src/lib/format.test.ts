import { ago, fmt, short, usd } from "./format";

describe("format", () => {
  it("abbreviates counts", () => {
    expect(fmt(950)).toBe("950");
    expect(fmt(1500)).toBe("1.5k");
    expect(fmt(25_000)).toBe("25k");
    expect(fmt(3_200_000)).toBe("3.2M");
  });
  it("renders relative time", () => {
    const now = Date.parse("2026-10-19T12:00:00Z");
    expect(ago("2026-10-19T11:59:30Z", now)).toBe("just now");
    expect(ago("2026-10-19T11:15:00Z", now)).toBe("45m ago");
    expect(ago("2026-10-19T07:00:00Z", now)).toBe("5h ago");
    expect(ago("2026-10-16T12:00:00Z", now)).toBe("3d ago");
    expect(ago(null, now)).toBe("—");
    expect(ago("not a date", now)).toBe("—");
  });
  it("shortens ids and formats dollars", () => {
    expect(short("2a5a3005-3747-45b9")).toBe("2a5a3005");
    expect(usd(0.034)).toBe("$0.034");
    expect(usd(0.5)).toBe("$0.50");
    expect(usd(12.345)).toBe("$12.35");
  });
});
