import { Route, Routes } from "react-router-dom";
import { BriefsBoard } from "./briefs/Board";
import { BriefDetailView } from "./briefs/Detail";

// Briefs (handoffs): board/list at /briefs, detail at /briefs/:id?owner=<user_id>.
export default function BriefsPage() {
  return (
    <Routes>
      <Route index element={<BriefsBoard />} />
      <Route path=":id" element={<BriefDetailView />} />
    </Routes>
  );
}
