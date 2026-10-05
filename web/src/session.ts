import { ApiError } from "@/lib/api.ts";
import { fetchMe, type SessionUser } from "@/lib/resources.ts";
import { useQuery } from "@tanstack/react-query";

export function useSession() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async (): Promise<SessionUser | null> => {
      try {
        return await fetchMe();
      } catch (error) {
        if (error instanceof ApiError && error.code === "unauthenticated") {
          return null;
        }
        throw error;
      }
    },
    retry: false,
  });
}
