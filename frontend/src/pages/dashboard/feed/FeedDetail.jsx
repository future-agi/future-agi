import React from "react";
import { Navigate, useParams } from "react-router-dom";

export default function FeedDetail() {
  const { id } = useParams();
  return <Navigate to={`/dashboard/error-feed/${id}`} replace />;
}
