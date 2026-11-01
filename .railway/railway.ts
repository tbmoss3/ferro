import { defineRailway, github, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const data = volume("ferro-data", { sizeMB: 1024, region: "us-west2" });

  const ferro = service("ferro", {
    source: github("tbmoss3/ferro", { branch: "main", checkSuites: false }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    volumeMounts: { "/app/data": data },
  });

  const cam = service("ferro-cam", {
    source: github("tbmoss3/ferro", { branch: "main", checkSuites: false }),
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile.cam" },
  });

  return project("Ferro", {
    variables: { managed: false },
    resources: [ferro, cam, data],
  });
});
