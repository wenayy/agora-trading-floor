// Small accessible primitives built on Radix (the same primitives shadcn/ui and 21st.dev components use),
// styled with the dashboard's own tokens rather than a stock theme.
import * as Switch from "@radix-ui/react-switch";
import * as Tooltip from "@radix-ui/react-tooltip";
import { animate } from "motion";
import { useEffect, useRef, useState, type ReactNode } from "react";

export function Hint({ text, children }: { text: string; children: ReactNode }) {
  return (
    <Tooltip.Root delayDuration={250}>
      <Tooltip.Trigger asChild>
        <span className="hint" tabIndex={0}>{children}</span>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content className="hint-content" side="top" sideOffset={6} collisionPadding={12}>
          {text}
          <Tooltip.Arrow className="hint-arrow" />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

export const HintProvider = Tooltip.Provider;

export function Toggle({ checked, onChange, label, disabled }: {
  checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean;
}) {
  return (
    <label className={`toggle ${disabled ? "disabled" : ""}`}>
      <Switch.Root className="switch" checked={checked} onCheckedChange={onChange} disabled={disabled}>
        <Switch.Thumb className="switch-thumb" />
      </Switch.Root>
      <span>{label}</span>
    </label>
  );
}

/** Tweens from the previous value to the new one so a change reads as a change, not a flicker. */
export function AnimatedNumber({ value, format }: { value: number; format: (v: number) => string }) {
  const [shown, setShown] = useState(value);
  const prev = useRef(value);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches || prev.current === value) {
      setShown(value);
      prev.current = value;
      return;
    }
    const controls = animate(prev.current, value, {
      duration: 0.6, ease: [0.22, 1, 0.36, 1], onUpdate: (v) => setShown(v),
    });
    prev.current = value;
    return () => controls.stop();
  }, [value]);
  return <>{format(shown)}</>;
}
