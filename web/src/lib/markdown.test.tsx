import { render } from "@testing-library/react";
import { Markdown } from "./markdown";

describe("Markdown", () => {
  it("renders headings, lists, code and inline marks", () => {
    const { container } = render(<Markdown source={"# Title\n\nSome **bold** and `code`.\n\n- one\n- two\n\n```\nx = 1\n```"} />);
    expect(container.querySelector("h1")?.textContent).toBe("Title");
    expect(container.querySelector("strong")?.textContent).toBe("bold");
    expect(container.querySelector("p code")?.textContent).toBe("code");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("pre code")?.textContent).toBe("x = 1");
  });
  it("never turns content into markup", () => {
    const { container } = render(<Markdown source={'<img src=x onerror="alert(1)"> <script>bad()</script>'} />);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<img src=x");
  });
});
