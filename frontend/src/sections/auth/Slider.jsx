import React from "react";
import { Swiper, SwiperSlide } from "swiper/react";
import { Pagination, Autoplay } from "swiper/modules";
import "swiper/css";
import "swiper/css/pagination";
import "swiper/css/navigation";
import { Box, Typography, useTheme } from "@mui/material";
import { slides } from "./contant";
import "./Slider.css";
export default function MUIWithSwiper() {
  const theme = useTheme();
  const isDark = theme.palette.mode === "dark";

  return (
    <Swiper
      className="mySwiper"
      pagination={true}
      loop={true}
      autoplay={{
        delay: 2500,
        disableOnInteraction: false,
      }}
      spaceBetween={100}
      speed={1000}
      modules={[Pagination, Autoplay]}
    >
      {slides.map((s, i) => (
        <SwiperSlide key={i}>
          <Box
            sx={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 4,
              color: "text.primary",
              borderRadius: 2,
              py: 5,
            }}
          >
            {/* Image Section */}
            <Box
              component="img"
              src={isDark && s.darkImageUrl ? s.darkImageUrl : s.imageUrl}
              alt={s.title}
              sx={{
                width: "509px",
                height: "450px",
                borderRadius: 2,
                objectFit: "cover",
              }}
            />

            {/* Text Section */}
            <Box
              sx={{
                display: "flex",
                flexDirection: "column",
                maxWidth: "500px",
              }}
            >
              <Typography sx={{ fontSize: "25px", fontWeight: 500, mb: 1 }}>
                {s.title}
              </Typography>
              <Typography sx={{ fontWeight: 400, fontSize: "20px", mb: 4 }}>
                {s.description}
              </Typography>
            </Box>
          </Box>
        </SwiperSlide>
      ))}
    </Swiper>
  );
}
