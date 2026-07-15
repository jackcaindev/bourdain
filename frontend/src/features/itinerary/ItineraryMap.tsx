import { useEffect } from 'react'
import L, { type LatLngTuple } from 'leaflet'
import markerIcon from 'leaflet/dist/images/marker-icon.png'
import markerIcon2x from 'leaflet/dist/images/marker-icon-2x.png'
import markerShadow from 'leaflet/dist/images/marker-shadow.png'
import 'leaflet/dist/leaflet.css'
import { MapContainer, Marker, Popup, TileLayer, useMap } from 'react-leaflet'
import type { ScoredRecommendation } from '../../lib/types'

L.Icon.Default.mergeOptions({
  iconRetinaUrl: markerIcon2x,
  iconUrl: markerIcon,
  shadowUrl: markerShadow,
})

function FitBounds({ coordinates }: { coordinates: LatLngTuple[] }) {
  const map = useMap()

  useEffect(() => {
    map.fitBounds(coordinates, { maxZoom: 15, padding: [24, 24] })
  }, [coordinates, map])

  return null
}

export function ItineraryMap({
  recommendations,
}: {
  recommendations: ScoredRecommendation[]
}) {
  const pinnedRecommendations = recommendations.filter(
    (recommendation) => recommendation.lat !== null && recommendation.lng !== null,
  )

  if (pinnedRecommendations.length === 0) {
    return null
  }

  const coordinates: LatLngTuple[] = pinnedRecommendations.map((recommendation) => [
    recommendation.lat as number,
    recommendation.lng as number,
  ])

  return (
    <MapContainer className="itinerary-map" style={{ height: '28rem' }}>
      <TileLayer
        attribution="© OpenStreetMap contributors"
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <FitBounds coordinates={coordinates} />
      {pinnedRecommendations.map((recommendation) => (
        <Marker
          key={recommendation.id}
          position={[recommendation.lat as number, recommendation.lng as number]}
        >
          <Popup>
            <strong>{recommendation.name}</strong>
            <br />
            {recommendation.category}
          </Popup>
        </Marker>
      ))}
    </MapContainer>
  )
}
