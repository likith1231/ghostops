"use client";

import { useEffect, useState } from "react";
import { api, type Status } from "./api";

export interface BackendState {
  /** null until the first check completes. */
  online: boolean | null;
  status: Status | null;
}

/** Poll /api/status so every section knows whether the backend is up and in mock mode. */
export function useBackend(intervalMs = 5000): BackendState {
  const [state, setState] = useState<BackendState>({ online: null, status: null });

  useEffect(() => {
    let alive = true;
    const check = async () => {
      try {
        const status = await api.status();
        if (alive) setState({ online: true, status });
      } catch {
        if (alive) setState({ online: false, status: null });
      }
    };
    check();
    const id = setInterval(check, intervalMs);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [intervalMs]);

  return state;
}
