


    // !!! This file is generated using emlearn !!!

    #include <stdint.h>
    

static inline int32_t dt_r1_d4_fp32_tree_0(const float *features, int32_t features_length) {
          if (features[0] < 0.360167f) {
              if (features[2] < 0.276552f) {
                  if (features[4] < -0.016338f) {
                      if (features[4] < -1.048745f) {
                          return 0;
                      } else {
                          return 2;
                      }
                  } else {
                      if (features[8] < 0.037818f) {
                          return 0;
                      } else {
                          return 0;
                      }
                  }
              } else {
                  if (features[6] < -0.643153f) {
                      if (features[2] < 0.508438f) {
                          return 1;
                      } else {
                          return 2;
                      }
                  } else {
                      if (features[6] < -0.522469f) {
                          return 1;
                      } else {
                          return 1;
                      }
                  }
              }
          } else {
              if (features[2] < 2.056433f) {
                  if (features[4] < 1.813361f) {
                      if (features[2] < 0.070358f) {
                          return 1;
                      } else {
                          return 2;
                      }
                  } else {
                      if (features[5] < 0.200441f) {
                          return 2;
                      } else {
                          return 1;
                      }
                  }
              } else {
                  if (features[5] < 0.743111f) {
                      if (features[8] < -0.333027f) {
                          return 1;
                      } else {
                          return 2;
                      }
                  } else {
                      if (features[0] < 1.222065f) {
                          return 1;
                      } else {
                          return 2;
                      }
                  }
              }
          }
        }
        

int32_t dt_r1_d4_fp32_predict(const float *features, int32_t features_length) {

        int32_t votes[3] = {0,};
        int32_t _class = -1;

        _class = dt_r1_d4_fp32_tree_0(features, features_length); votes[_class] += 1;
    
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
    

int dt_r1_d4_fp32_predict_proba(const float *features, int32_t features_length, float *out, int out_length) {

        int32_t _class = -1;

        for (int i=0; i<out_length; i++) {
            out[i] = 0.0f;
        }

        _class = dt_r1_d4_fp32_tree_0(features, features_length); out[_class] += 1.0f;
    
        // compute mean
        for (int i=0; i<out_length; i++) {
            out[i] = out[i] / 1;
        }
        return 0;
    }
    