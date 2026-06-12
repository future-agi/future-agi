import React from "react";
import { Helmet } from "react-helmet-async";
import { useParams } from "src/routes/hooks";
import UserDetailView from "src/sections/user/view/user-detail-view";

export default function UserDetailPage() {
  const params = useParams();
  const { id } = params;

  return (
    <>
      <Helmet>
        <title> Dashboard: User Details</title>
      </Helmet>

      <UserDetailView id={id} />
    </>
  );
}
