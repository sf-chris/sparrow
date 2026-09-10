import { useId, useState } from "react";
import { Eye, EyeOff } from "lucide-react";

export default function PasswordField({
  label = "Password",
  value,
  onChange,
  creating = false,
  hint,
}: {
  label?: string;
  value: string;
  onChange: (value: string) => void;
  creating?: boolean;
  hint?: string;
}) {
  const id = useId();
  const [visible, setVisible] = useState(false);
  return (
    <div className="sp-field">
      <label htmlFor={id}>{label}</label>
      <div className="sp-password">
        <input
          id={id}
          required
          type={visible ? "text" : "password"}
          minLength={creating ? 8 : undefined}
          maxLength={1024}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete={creating ? "new-password" : "current-password"}
          aria-describedby={hint ? `${id}-hint` : undefined}
        />
        <button
          type="button"
          onClick={() => setVisible(!visible)}
          aria-label={`${visible ? "Hide" : "Show"} ${label.toLowerCase()}`}
          aria-pressed={visible}
        >
          {visible ? <EyeOff size={17} /> : <Eye size={17} />}
        </button>
      </div>
      {hint && <small id={`${id}-hint`}>{hint}</small>}
    </div>
  );
}
