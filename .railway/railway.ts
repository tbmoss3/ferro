import { defineRailway, github, project, service } from "railway/iac";

export default defineRailway(() => {
  const ferro = service("ferro", {
    source: github("tbmoss3/ferro", { branch: "main", checkSuites: false }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
  });

  const cam = service("ferro-cam", {
    source: github("tbmoss3/ferro", { branch: "main", checkSuites: false }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile.cam" },
  });

  return project("Ferro", {
    variables: { managed: false },
    resources: [ferro, cam],
  });
});
