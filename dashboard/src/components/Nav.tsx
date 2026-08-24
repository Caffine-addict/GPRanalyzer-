import { NavLink } from "react-router-dom";

// No auth on the backend today (a documented internal-tool trust model —
// see api/server.py's review notes), so this is a view switcher, not an
// access-control boundary: it doesn't gate what a visitor can reach, only
// which lens they're currently looking through.
export function Nav() {
  const linkStyle = ({ isActive }: { isActive: boolean }) => ({
    marginRight: "1rem",
    fontWeight: isActive ? 700 : 400,
  });

  return (
    <nav style={{ padding: "0.75rem 1rem", borderBottom: "1px solid #ddd" }}>
      <strong style={{ marginRight: "2rem" }}>gpr-analyzer</strong>
      <NavLink to="/operator" style={linkStyle}>
        Operator
      </NavLink>
      <NavLink to="/manager" style={linkStyle}>
        Manager
      </NavLink>
      <NavLink to="/pm" style={linkStyle}>
        PM
      </NavLink>
    </nav>
  );
}
