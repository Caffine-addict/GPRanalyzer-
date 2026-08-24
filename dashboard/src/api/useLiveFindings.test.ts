import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLiveFindings } from "./useLiveFindings";
import type { FindingEvent } from "./types";

class FakeWebSocket {
  static instances: FakeWebSocket[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closeCalls = 0;

  constructor(url: string) {
    this.url = url;
    FakeWebSocket.instances.push(this);
  }

  close(): void {
    this.closeCalls += 1;
    this.onclose?.();
  }
}

const sampleEvent: FindingEvent = {
  type: "finding.created",
  survey_id: "s1",
  finding: {
    evidence: {
      detection_class: "cavities",
      detection_confidence: 0.8,
      depth_m: null,
      depth_confidence: "unavailable",
      position_m: null,
      position_confidence: "unavailable",
      amplitude: null,
      amplitude_confidence: "unavailable",
      hyperbola_width_px: 10,
      neighbours: [],
    },
    risk_level: "HIGH",
    risk_score: 0.7,
    risk_rules_fired: [],
    what: null,
    where: null,
    why: null,
    how: null,
    recommended_action: null,
    reasoning_latency_ms: null,
  },
};

describe("useLiveFindings", () => {
  beforeEach(() => {
    FakeWebSocket.instances = [];
    vi.stubGlobal("WebSocket", FakeWebSocket as unknown as typeof WebSocket);
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("does not open a socket while disabled", () => {
    renderHook(() => useLiveFindings(false));
    expect(FakeWebSocket.instances).toHaveLength(0);
  });

  it("opens a socket and transitions connecting -> open", () => {
    const { result } = renderHook(() => useLiveFindings(true));
    expect(result.current.connectionState).toBe("connecting");

    act(() => {
      FakeWebSocket.instances[0].onopen?.();
    });

    // act() flushes the resulting state update synchronously — no need to
    // poll with waitFor (which uses real timers and would hang under the
    // fake timers this suite installs for the reconnect-backoff tests).
    expect(result.current.connectionState).toBe("open");
  });

  it("appends a parsed message to events", () => {
    const { result } = renderHook(() => useLiveFindings(true));

    act(() => {
      FakeWebSocket.instances[0].onmessage?.({ data: JSON.stringify(sampleEvent) } as MessageEvent<string>);
    });

    expect(result.current.events).toEqual([sampleEvent]);
  });

  it("ignores a malformed message instead of crashing", async () => {
    const { result } = renderHook(() => useLiveFindings(true));

    act(() => {
      FakeWebSocket.instances[0].onmessage?.({ data: "not json" } as MessageEvent<string>);
    });

    // Give React a tick; nothing should have been appended and no throw occurred.
    await act(async () => {});
    expect(result.current.events).toEqual([]);
  });

  it("clear() empties the events list", () => {
    const { result } = renderHook(() => useLiveFindings(true));
    act(() => {
      FakeWebSocket.instances[0].onmessage?.({ data: JSON.stringify(sampleEvent) } as MessageEvent<string>);
    });
    expect(result.current.events).toHaveLength(1);

    act(() => result.current.clear());
    expect(result.current.events).toEqual([]);
  });

  it("closes the socket on error (onclose then drives the actual reconnect)", () => {
    renderHook(() => useLiveFindings(true));
    const socket = FakeWebSocket.instances[0];

    act(() => {
      socket.onerror?.();
    });

    expect(socket.closeCalls).toBe(1);
  });

  it("reconnects after a close, with a backoff delay", async () => {
    renderHook(() => useLiveFindings(true));
    expect(FakeWebSocket.instances).toHaveLength(1);

    act(() => {
      FakeWebSocket.instances[0].onclose?.();
    });
    // Not yet reconnected — still waiting out the backoff delay.
    expect(FakeWebSocket.instances).toHaveLength(1);

    act(() => {
      vi.advanceTimersByTime(600);
    });
    expect(FakeWebSocket.instances).toHaveLength(2);
  });

  it("caps the reconnect backoff instead of doubling forever", () => {
    // Confirmed by direct mutation before writing this test: removing the
    // Math.min(..., MAX_RECONNECT_DELAY_MS) cap left all other tests in
    // this file passing — a single close/reconnect cycle alone never
    // distinguishes capped from uncapped growth, only enough consecutive
    // cycles to reach the cap does. 500 -> 1000 -> 2000 -> 4000 -> 8000 ->
    // (would be 16000, capped to 10000) -> (would be 20000, must stay 10000).
    renderHook(() => useLiveFindings(true));

    for (const delay of [500, 1000, 2000, 4000, 8000, 10_000]) {
      const before = FakeWebSocket.instances.length;
      act(() => {
        FakeWebSocket.instances[before - 1].onclose?.();
      });
      act(() => {
        vi.advanceTimersByTime(delay);
      });
      expect(FakeWebSocket.instances).toHaveLength(before + 1);
    }

    // One more cycle: an uncapped implementation would schedule this one at
    // 20000ms, so advancing only 10000ms would NOT be enough to reconnect.
    const before = FakeWebSocket.instances.length;
    act(() => {
      FakeWebSocket.instances[before - 1].onclose?.();
    });
    act(() => {
      vi.advanceTimersByTime(10_000);
    });
    expect(FakeWebSocket.instances).toHaveLength(before + 1);
  });

  it("leaves no dangling reconnect timer scheduled after unmount", () => {
    // Distinguishes the onclose handler's own `if (cancelled) return` guard
    // from connect()'s separate guard, which a plain "no new socket appears
    // after unmount" assertion can't do: FakeWebSocket.close() runs onclose
    // synchronously during cleanup, after `cancelled = true` is already set,
    // so even with the onclose guard removed, connect()'s own guard still
    // prevents a new socket — the observable difference is a reconnect
    // timer left dangling (never cleared, fires later for nothing) vs none
    // scheduled at all.
    const { unmount } = renderHook(() => useLiveFindings(true));
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("closes the socket and does not reconnect once disabled/unmounted", () => {
    const { unmount } = renderHook(() => useLiveFindings(true));
    const socket = FakeWebSocket.instances[0];

    unmount();

    expect(socket.closeCalls).toBe(1);
    act(() => {
      vi.advanceTimersByTime(5000);
    });
    expect(FakeWebSocket.instances).toHaveLength(1); // no reconnect after unmount
  });
});
