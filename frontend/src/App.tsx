import { BrowserRouter, Link, Outlet, Route, Routes } from 'react-router'
import { ItineraryView } from './features/itinerary/ItineraryView'
import { KickoffForm } from './features/kickoff/KickoffForm'
import { ProgressFeed } from './features/progress/ProgressFeed'
import { SelectionScreen } from './features/selection/SelectionScreen'

function BriefLayout() {
  return (
    <div className="app-shell">
      <header className="masthead">
        <Link to="/">The Bourdain Brief</Link>
        <span>TRAVEL, WITHOUT THE TOURIST GLOSS</span>
      </header>
      <Outlet />
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<BriefLayout />}>
          <Route index element={<KickoffForm />} />
          <Route path="brief/:sessionId/progress" element={<ProgressFeed />} />
          <Route path="brief/:sessionId/select" element={<SelectionScreen />} />
          <Route path="brief/:sessionId/itinerary" element={<ItineraryView />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
