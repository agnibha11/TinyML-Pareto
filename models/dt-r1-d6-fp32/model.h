


    // !!! This file is generated using emlearn !!!

    #include <stdint.h>
    

static inline int32_t dt_r1_d6_fp32_tree_0(const float *features, int32_t features_length) {
          if (features[1] < 0.480359f) {
              if (features[1] < 0.079067f) {
                  if (features[1] < -0.986686f) {
                      return 0;
                  } else {
                      if (features[6] < 0.480248f) {
                          if (features[0] < -0.439063f) {
                              if (features[1] < -0.708470f) {
                                  return 6;
                              } else {
                                  return 2;
                              }
                          } else {
                              if (features[6] < -0.621903f) {
                                  return 2;
                              } else {
                                  return 5;
                              }
                          }
                      } else {
                          if (features[7] < 2.026219f) {
                              if (features[2] < -0.691551f) {
                                  return 5;
                              } else {
                                  return 4;
                              }
                          } else {
                              if (features[0] < -0.303568f) {
                                  return 4;
                              } else {
                                  return 5;
                              }
                          }
                      }
                  }
              } else {
                  if (features[2] < 0.103303f) {
                      if (features[1] < 0.101586f) {
                          return 1;
                      } else {
                          return 8;
                      }
                  } else {
                      return 1;
                  }
              }
          } else {
              if (features[8] < 1.081430f) {
                  if (features[8] < 0.507627f) {
                      if (features[7] < 0.182788f) {
                          if (features[5] < 0.011841f) {
                              return 3;
                          } else {
                              return 7;
                          }
                      } else {
                          return 7;
                      }
                  } else {
                      if (features[7] < 1.097329f) {
                          return 3;
                      } else {
                          return 7;
                      }
                  }
              } else {
                  return 9;
              }
          }
        }
        

int32_t dt_r1_d6_fp32_predict(const float *features, int32_t features_length) {

        int32_t votes[10] = {0,};
        int32_t _class = -1;

        _class = dt_r1_d6_fp32_tree_0(features, features_length); votes[_class] += 1;
    
        int32_t most_voted_class = -1;
        int32_t most_voted_votes = 0;
        for (int32_t i=0; i<10; i++) {

            if (votes[i] > most_voted_votes) {
                most_voted_class = i;
                most_voted_votes = votes[i];
            }
        }
        return most_voted_class;
    }
    

int dt_r1_d6_fp32_predict_proba(const float *features, int32_t features_length, float *out, int out_length) {

        int32_t _class = -1;

        for (int i=0; i<out_length; i++) {
            out[i] = 0.0f;
        }

        _class = dt_r1_d6_fp32_tree_0(features, features_length); out[_class] += 1.0f;
    
        // compute mean
        for (int i=0; i<out_length; i++) {
            out[i] = out[i] / 1;
        }
        return 0;
    }
    