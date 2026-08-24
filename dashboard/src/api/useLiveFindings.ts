import { useEffect, useState } from "react";
import { websocketUrl } from "./client";
import type { FindingEvent } from "./types";

export type ConnectionState = "connecting" | "open" | "closed";

const INITIAL_RECONNECT_DELAY_MS = 500;
const MAX_RECONNECT_DELAY_MS = 10_000;

export interface LiveFindings {
  events: FindingEvent[];
  connectionState: ConnectionState;
  clear: () => void;
}

// One WebSocket connection to /ws/live, reconnecting with exponential
// backoff on drop. `enabled=false` tears the connection down entirely
// (views that don't need the live feed shouldn't hold a socket open).
export function useLiveFindings(enabled: boolean): LiveFindings {
  const [events, setEvents] = useState<FindingEvent[]>([]);
  const [connectionState, setConnectionState] = useState<ConnectionState>("closed");

  useEffect(() => {
    if (!enabled) {
      setConnectionState("closed");
      return;
    }

    let cancelled = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;

    const connect = () => {
      if (cancelled) return;
      setConnectionState("connecting");
      socket = new WebSocket(websocketUrl());

      socket.onopen = () => {
        reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;
        setConnectionState("open");
      };

      socket.onmessage = (event: MessageEvent<string>) => {
        try {
          const parsed = JSON.parse(event.data) as FindingEvent;
          setEvents((prev) => [...prev, parsed]);
        } catch {
          // A malformed message must not take down the live feed.
        }
      };

      socket.onclose = () => {
        if (cancelled) return;
        setConnectionState("closed");
        reconnectTimer = setTimeout(() => {
          reconnectDelayMs = Math.min(reconnectDelayMs * 2, MAX_RECONNECT_DELAY_MS);
          connect();
        }, reconnectDelayMs);
      };

      socket.onerror = () => {
        // onclose always fires after onerror for a WebSocket — reconnect
        // logic lives there, not duplicated here.
        socket?.close();
      };
    };

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [enabled]);

  return {
    events,
    connectionState,
    clear: () => setEvents([]),
  };
}
