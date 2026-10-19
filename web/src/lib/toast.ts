import { createContext, useContext } from "react";

export const ToastContext = createContext<(msg: string) => void>(() => {});
/** Show a short status message (rendered by ToastProvider). */
export const useToast = (): ((msg: string) => void) => useContext(ToastContext);
