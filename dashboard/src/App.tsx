import { Navigate, Route, Routes } from "react-router-dom";
import { Nav } from "./components/Nav";
import { OperatorView } from "./views/OperatorView";
import { ManagerView } from "./views/ManagerView";
import { PmView } from "./views/PmView";

function App() {
  return (
    <>
      <Nav />
      <Routes>
        <Route path="/" element={<Navigate to="/operator" replace />} />
        <Route path="/operator" element={<OperatorView />} />
        <Route path="/manager" element={<ManagerView />} />
        <Route path="/pm" element={<PmView />} />
      </Routes>
    </>
  );
}

export default App;
