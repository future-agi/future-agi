import { describe, it, expect } from "vitest";
import { render, screen, userEvent } from "src/utils/test-utils";
import Tag from "../parts/Tag";
import Field from "../parts/Field";
import Pane from "../parts/Pane";
import XCard from "../parts/XCard";
import DataTable from "../parts/DataTable";

describe("Tag", () => {
  it("shows its label", () => {
    render(<Tag kind="pass">validated</Tag>);
    expect(screen.getByText("validated")).toBeInTheDocument();
  });
});

describe("Field", () => {
  it("labels its content", () => {
    render(<Field label="the person is told">order a latte</Field>);
    expect(screen.getByText("the person is told")).toBeInTheDocument();
    expect(screen.getByText("order a latte")).toBeInTheDocument();
  });
});

describe("Pane", () => {
  it("carries a title and the meta line that explains it", () => {
    render(<Pane title="Tools" meta="5 the agent really has">body</Pane>);
    expect(screen.getByText("Tools")).toBeInTheDocument();
    expect(screen.getByText("5 the agent really has")).toBeInTheDocument();
  });
});

describe("XCard", () => {
  it("shows title and meta in its summary", () => {
    render(<XCard title="place_order" meta="12 lines">body</XCard>);
    expect(screen.getByText("place_order")).toBeInTheDocument();
    expect(screen.getByText("12 lines")).toBeInTheDocument();
  });

  it("can start open when the reader needs to see it", () => {
    const { container } = render(<XCard title="broken" open>body</XCard>);
    expect(container.querySelector("details")).toHaveAttribute("open");
  });
});

describe("DataTable", () => {
  const columns = ["id", "item"];
  const rows = [
    { id: "1", item: "latte" },
    { id: "2", item: "mango smoothie" },
  ];

  it("renders rows", () => {
    render(<DataTable columns={columns} rows={rows} count={2} />);
    expect(screen.getByText("mango smoothie")).toBeInTheDocument();
  });

  it("shows an array by its count first", () => {
    render(<DataTable columns={["items"]} rows={[{ items: ["a", "b", "c"] }]} count={1} />);
    expect(screen.getByText(/^\[3\]/)).toBeInTheDocument();
  });

  it("says when it is showing fewer rows than exist", () => {
    render(<DataTable columns={columns} rows={rows} count={940} />);
    expect(screen.getByText(/showing 2 of 940/)).toBeInTheDocument();
  });

  it("filters once there are enough rows to need it", async () => {
    const many = Array.from({ length: 8 }, (_, i) => ({ id: String(i), item: `item-${i}` }));
    render(<DataTable columns={columns} rows={many} count={8} />);
    await userEvent.type(screen.getByPlaceholderText("filter rows"), "item-3");
    expect(screen.getByText("item-3")).toBeInTheDocument();
    expect(screen.queryByText("item-5")).not.toBeInTheDocument();
  });
});
