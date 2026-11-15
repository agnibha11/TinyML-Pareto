


    // !!! This file is generated using emlearn !!!

    #include <stdint.h>
    

static inline int32_t dt_r4_d4_fp32_tree_0(const float *features, int32_t features_length) {
          if (features[15] < 0.811350f) {
              if (features[13] < 0.937096f) {
                  if (features[3] < 0.921527f) {
                      if (features[20] < 1.354214f) {
                          return 1;
                      } else {
                          return 2;
                      }
                  } else {
                      if (features[4] < -0.060184f) {
                          return 1;
                      } else {
                          return 1;
                      }
                  }
              } else {
                  if (features[3] < -0.376416f) {
                      if (features[0] < -1.360969f) {
                          return 0;
                      } else {
                          return 2;
                      }
                  } else {
                      if (features[0] < 0.254303f) {
                          return 0;
                      } else {
                          return 1;
                      }
                  }
              }
          } else {
              if (features[1] < -0.382089f) {
                  if (features[0] < -0.616332f) {
                      if (features[5] < -0.604830f) {
                          return 0;
                      } else {
                          return 2;
                      }
                  } else {
                      if (features[6] < 0.450567f) {
                          return 1;
                      } else {
                          return 2;
                      }
                  }
              } else {
                  if (features[21] < -0.056735f) {
                      if (features[7] < -0.266717f) {
                          return 1;
                      } else {
                          return 2;
                      }
                  } else {
                      if (features[5] < 0.802436f) {
                          return 2;
                      } else {
                          return 1;
                      }
                  }
              }
          }
        }
        

int32_t dt_r4_d4_fp32_predict(const float *features, int32_t features_length) {

        int32_t votes[3] = {0,};
        int32_t _class = -1;

        _class = dt_r4_d4_fp32_tree_0(features, features_length); votes[_class] += 1;
    
        int32_t most_voted_class = -1;
        int32_t most_voted_votes = 0;
        for (int32_t i=0; i<3; i++) {

            if (votes[i] > most_voted_votes) {
                most_voted_class = i;
                most_voted_votes = votes[i];
            }
        }
        return most_voted_class;
    }
    

int dt_r4_d4_fp32_predict_proba(const float *features, int32_t features_length, float *out, int out_length) {

        int32_t _class = -1;

        for (int i=0; i<out_length; i++) {
            out[i] = 0.0f;
        }

        _class = dt_r4_d4_fp32_tree_0(features, features_length); out[_class] += 1.0f;
    
        // compute mean
        for (int i=0; i<out_length; i++) {
            out[i] = out[i] / 1;
        }
        return 0;
    }
    