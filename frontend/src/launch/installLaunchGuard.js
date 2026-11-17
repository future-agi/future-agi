// L6: main.jsx imports this once. In a launch (non-prototype) build it
// installs the absent-path guard on the app's axios instance; prototype
// builds and unit tests install nothing (absentPathGuard.js).
import axiosInstance from "src/utils/axios";
import { installAbsentPathGuard } from "./absentPathGuard";

installAbsentPathGuard(axiosInstance);
