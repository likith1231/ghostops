import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import MemoryChart from "@/components/MemoryChart";

const MiB = 1024 * 1024;

describe("MemoryChart", () => {
  it("renders nothing with fewer than two samples", () => {
    const { container } = render(<MemoryChart metrics={{ memory_working_set: [{ value: 5 }] }} fixed={null} />);
    expect(container.innerHTML).toBe("");
  });

  it("draws the samples and the limit", () => {
    const { container, getByText } = render(
      <MemoryChart metrics={{ memory_working_set: [{ value: 27 * MiB }, { value: 80 * MiB }], memory_limit_bytes: [{ value: 128 * MiB }] }} fixed={null} />,
    );
    expect(container.querySelectorAll("circle")).toHaveLength(2);
    expect(getByText("limit 128 MiB")).toBeTruthy();
    expect(getByText("Samples the pipeline received with the alert.")).toBeTruthy();
  });

  it("adds the projected line only once a fix is validated", () => {
    const metrics = { memory_working_set: [{ value: 1 * MiB }, { value: 9 * MiB }] };
    const before = render(<MemoryChart metrics={metrics} fixed={null} />);
    expect(before.container.querySelector('path[stroke="#4ade80"]')).toBeNull();
    const after = render(<MemoryChart metrics={metrics} fixed={true} />);
    expect(after.container.querySelector('path[stroke="#4ade80"]')).not.toBeNull();
  });
});
